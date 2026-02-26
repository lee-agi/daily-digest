"""CCF Best Paper Collector.

Monitors CCF-A AI conferences (plus ICLR) for best/award papers.

Strategy:
- ccf-deadlines YAML as conference directory (latest year/edition metadata)
- OpenReview API for oral papers (NeurIPS, ICML, ICLR)
- Website scraping for non-OpenReview conferences (AAAI, CVPR, ACL)
- IJCAI has no reliable best paper page, so remains a stub
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
import yaml
from bs4 import BeautifulSoup, Tag

from collectors.base import BaseCollector
from schema import ContentItem, SourceType

logger = logging.getLogger(__name__)

CCF_DEADLINES_RAW = (
    "https://raw.githubusercontent.com/ccfddl/ccf-deadlines/"
    "main/conference/AI/{key}.yml"
)
OPENREVIEW_API = "https://api2.openreview.net/notes"

AAAI_AWARDS_URL = (
    "https://aaai.org/about-aaai/aaai-awards/"
    "aaai-conference-paper-awards-and-recognition/"
)
CVPR_AWARDS_URL = "https://cvpr.thecvf.com/Conferences/{year}/BestPapersDemos"
ACL_AWARDS_URL = "https://{year}.aclweb.org/program/best_papers/"

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


@dataclass
class ConferenceInfo:
    """Static conference metadata."""
    name: str
    yml_key: str
    on_openreview: bool
    oral_venue_case: str = "Oral"  # "Oral" or "oral"


@dataclass
class ConferenceEdition:
    """A specific year/edition of a conference parsed from YAML."""
    name: str
    year: int
    conf_url: str = ""
    conf_date: str = ""
    dblp: str = ""


# CCF-A AI conferences + ICLR (rank N in ccf-deadlines)
CONFERENCES = [
    ConferenceInfo("NeurIPS", "nips", True, "oral"),
    ConferenceInfo("ICML", "icml", True, "oral"),
    ConferenceInfo("ICLR", "iclr", True, "Oral"),
    ConferenceInfo("AAAI", "aaai", False),
    ConferenceInfo("CVPR", "cvpr", False),
    ConferenceInfo("IJCAI", "ijcai", False),
    ConferenceInfo("ACL", "acl", False),
]


class CcfBestPaperCollector(BaseCollector):
    """Collect oral/best papers from CCF-A AI conferences."""

    source_name = "ccf_bestpaper"
    source_type = SourceType.API

    async def collect(self) -> list[ContentItem]:
        """Main collection pipeline."""
        # Step 1: Fetch conference metadata (latest editions)
        editions = await self._fetch_conference_metadata()
        conf_latest: dict[str, ConferenceEdition] = {}
        for edition in editions:
            existing = conf_latest.get(edition.name)
            if existing is None or edition.year > existing.year:
                conf_latest[edition.name] = edition

        # Step 2: Collect papers from each conference
        all_items: list[ContentItem] = []
        seen_urls: set[str] = set()

        for conf in CONFERENCES:
            edition = conf_latest.get(conf.name)
            year = edition.year if edition else datetime.now().year

            try:
                if conf.on_openreview:
                    items = await self._fetch_openreview_orals(
                        conf.name, year, conf.oral_venue_case
                    )
                else:
                    items = await self._fetch_website_awards(conf.name, year)
            except Exception:
                logger.exception("[ccf_bestpaper] Failed to collect %s %d",
                                 conf.name, year)
                continue

            # URL dedup
            for item in items:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    all_items.append(item)

        logger.info("[ccf_bestpaper] Collected %d total papers", len(all_items))
        return all_items

    async def _fetch_conference_metadata(self) -> list[ConferenceEdition]:
        """Fetch ccf-deadlines YAML files and extract latest editions."""
        editions: list[ConferenceEdition] = []

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            tasks = [
                self._fetch_single_yaml(client, conf)
                for conf in CONFERENCES
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for conf, result in zip(CONFERENCES, results):
            if isinstance(result, Exception):
                logger.warning(
                    "[ccf_bestpaper] Failed to fetch YAML for %s: %s",
                    conf.name, result,
                )
                continue
            editions.extend(result)

        return editions

    async def _fetch_single_yaml(
        self, client: httpx.AsyncClient, conf: ConferenceInfo
    ) -> list[ConferenceEdition]:
        """Fetch and parse a single conference YAML file.

        ccf-deadlines YAML structure:
        - title: NeurIPS
          dblp: nips
          confs:
            - year: 2024
              link: https://...
              date: December 9-15, 2024
        """
        url = CCF_DEADLINES_RAW.format(key=conf.yml_key)
        resp = await client.get(url)
        resp.raise_for_status()

        data = yaml.safe_load(resp.text)
        if not isinstance(data, list) or not data:
            return []

        # Top-level is a single-element list with conference metadata
        conf_data = data[0]
        confs = conf_data.get("confs", [])
        dblp = conf_data.get("dblp", "")

        editions: list[ConferenceEdition] = []
        for entry in confs:
            year = entry.get("year")
            if not year:
                continue
            editions.append(ConferenceEdition(
                name=conf.name,
                year=int(year),
                conf_url=entry.get("link", ""),
                conf_date=entry.get("date", ""),
                dblp=dblp,
            ))

        return editions

    async def _fetch_openreview_orals(
        self, conf_name: str, year: int, oral_case: str
    ) -> list[ContentItem]:
        """Fetch oral papers from OpenReview API with pagination.

        Args:
            conf_name: Conference name (e.g. "ICLR", "NeurIPS").
            year: Conference year.
            oral_case: "Oral" or "oral" depending on venue tag convention.
        """
        venue_tag = f"{conf_name} {year} {oral_case}"
        items: list[ContentItem] = []
        limit = 25  # Smaller pages since details=replies inflates response size
        offset = 0

        # Read min_avg_rating from config (per-conference thresholds)
        min_avg_rating_cfg = self.config.get("min_avg_rating", {})
        min_avg_rating: float | None = min_avg_rating_cfg.get(conf_name)

        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            while True:
                params: dict[str, Any] = {
                    "content.venue": venue_tag,
                    "limit": limit,
                    "offset": offset,
                    "details": "replies",
                }
                try:
                    resp = await client.get(OPENREVIEW_API, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPError as e:
                    logger.error(
                        "[ccf_bestpaper] OpenReview API error for %s: %s",
                        venue_tag, e,
                    )
                    break

                notes = data.get("notes", [])
                for note in notes:
                    replies = note.get("details", {}).get("replies", [])
                    item = self._parse_openreview_note(
                        note, conf_name, year, replies=replies,
                    )
                    if item is None:
                        continue

                    # Rating filter: skip papers below threshold.
                    # Papers without public reviews (avg_rating=None) pass through.
                    if min_avg_rating is not None:
                        avg = item.extra.get("avg_rating")
                        if avg is not None and avg < min_avg_rating:
                            continue

                    items.append(item)

                if len(notes) < limit:
                    break
                offset += limit
                await asyncio.sleep(0.5)  # Rate-limit between pages

        filtered_msg = ""
        if min_avg_rating is not None:
            filtered_msg = f" (min_avg_rating={min_avg_rating})"
        logger.info(
            "[ccf_bestpaper] Fetched %d %s %d %s papers from OpenReview%s",
            len(items), conf_name, year, oral_case, filtered_msg,
        )
        return items

    @staticmethod
    def _extract_avg_rating(
        replies: list[dict],
    ) -> tuple[float | None, int, list[int]]:
        """Extract average rating from Official_Review replies.

        Returns:
            (avg_rating, num_reviews, individual_ratings)
            avg_rating is None when no public reviews with ratings exist.
        """
        ratings: list[int] = []
        for reply in replies:
            # Only consider Official_Review invitations.
            # API v2 uses "invitations" (list), v1 uses "invitation" (str).
            invitation = reply.get("invitation", "")
            invitations = reply.get("invitations", [])
            is_review = "Official_Review" in invitation or any(
                "Official_Review" in inv for inv in invitations
            )
            if not is_review:
                continue
            content = reply.get("content", {})
            rating_field = content.get("rating", {})

            # Handle three formats:
            # 1) dict: {"value": 8} or {"value": "8: Strong Accept"}
            # 2) int: 8
            # 3) str: "8: Strong Accept"
            if isinstance(rating_field, dict):
                raw = rating_field.get("value")
            else:
                raw = rating_field

            if raw is None:
                continue

            if isinstance(raw, int):
                ratings.append(raw)
            elif isinstance(raw, str):
                m = re.match(r"(\d+)", raw)
                if m:
                    ratings.append(int(m.group(1)))

        if not ratings:
            return None, 0, []
        avg = sum(ratings) / len(ratings)
        return avg, len(ratings), ratings

    def _parse_openreview_note(
        self,
        note: dict,
        conf_name: str,
        year: int,
        replies: list[dict] | None = None,
    ) -> ContentItem | None:
        """Parse an OpenReview note into a ContentItem."""
        content = note.get("content", {})
        forum_id = note.get("forum") or note.get("id", "")

        # Extract title
        title_field = content.get("title", {})
        title = title_field.get("value", "") if isinstance(title_field, dict) else str(title_field)
        if not title:
            return None

        # Extract authors
        authors_field = content.get("authors", {})
        authors_list = authors_field.get("value", []) if isinstance(authors_field, dict) else []
        author_str = ", ".join(authors_list[:5])
        if len(authors_list) > 5:
            author_str += " et al."

        # Extract abstract
        abstract_field = content.get("abstract", {})
        abstract = abstract_field.get("value", "") if isinstance(abstract_field, dict) else str(abstract_field)

        # Extract venue tag
        venue_field = content.get("venue", {})
        venue_tag = venue_field.get("value", "") if isinstance(venue_field, dict) else str(venue_field)

        # Parse timestamp (odate is milliseconds since epoch)
        odate = note.get("odate")
        if odate:
            published_at = datetime.fromtimestamp(odate / 1000, tz=timezone.utc)
        else:
            published_at = datetime.now(timezone.utc)

        # Extract reviewer ratings
        avg_rating, num_reviews, individual_ratings = self._extract_avg_rating(
            replies or []
        )

        return ContentItem(
            source="ccf_bestpaper",
            source_type=SourceType.API,
            title=f"[{conf_name} {year} Oral] {title}",
            url=f"https://openreview.net/forum?id={forum_id}",
            author=author_str,
            content=abstract[:1000],
            published_at=published_at,
            score=avg_rating if avg_rating is not None else 0.0,
            tags=[conf_name.lower(), "oral", str(year)],
            extra={
                "conference": conf_name,
                "year": year,
                "venue": venue_tag,
                "forum_id": forum_id,
                "avg_rating": avg_rating,
                "num_reviews": num_reviews,
                "individual_ratings": individual_ratings,
            },
        )

    async def _fetch_website_awards(
        self, conf_name: str, year: int
    ) -> list[ContentItem]:
        """Dispatch to per-conference website scrapers."""
        handlers = {
            "AAAI": self._fetch_aaai_awards,
            "CVPR": self._fetch_cvpr_awards,
            "ACL": self._fetch_acl_awards,
        }
        handler = handlers.get(conf_name)
        if handler:
            return await handler(year)
        logger.warning(
            "[ccf_bestpaper] No award scraper for %s %d", conf_name, year,
        )
        return []

    def _make_award_item(
        self,
        conf: str,
        year: int,
        title: str,
        author: str,
        award_type: str,
        url: str = "",
    ) -> ContentItem:
        """Create a ContentItem for an award paper."""
        return ContentItem(
            source="ccf_bestpaper",
            source_type=SourceType.HTTP_SCRAPE,
            title=f"[{conf} {year} {award_type}] {title}",
            url=url,
            author=author,
            published_at=datetime.now(timezone.utc),
            score=0.0,
            tags=[conf.lower(), "best_paper", str(year)],
            extra={
                "conference": conf,
                "year": year,
                "award_type": award_type,
            },
        )

    # ------------------------------------------------------------------
    # AAAI: single page with all years
    # Structure: H3=year, H4=award category, <p><strong>Title</strong>...
    # Two patterns for title+author:
    #   1) <p><strong>Title</strong><br>Authors</p>  (strong+br+text)
    #   2) <p><strong>Title</strong></p><p>Authors</p> (title p + author p)
    # ------------------------------------------------------------------

    async def _fetch_aaai_awards(self, year: int) -> list[ContentItem]:
        """Scrape AAAI awards page for a specific year."""
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True, headers=_HTTP_HEADERS,
        ) as client:
            resp = await client.get(AAAI_AWARDS_URL)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[ContentItem] = []

        # Find the year section: H3 with text matching year or "Past Recipients"
        # that contains a <strong> with "year" or text == "year"
        year_str = str(year)
        year_h3 = None
        for h3 in soup.find_all("h3"):
            text = h3.get_text(strip=True)
            if text == year_str:
                year_h3 = h3
                break

        if year_h3 is None:
            # Year might be under "Past Recipients" section — search <strong>
            # with text matching the year within <p> tags
            for strong in soup.find_all("strong"):
                if strong.get_text(strip=True) == year_str:
                    # Found the year marker; use its parent's next siblings
                    year_h3 = strong.parent
                    break

        if year_h3 is None:
            logger.warning(
                "[ccf_bestpaper] AAAI: year %d not found on awards page", year,
            )
            return []

        # Walk siblings from year_h3 to collect all H4 categories and papers
        current_award_type = "Outstanding Paper"
        sib = year_h3.next_sibling
        while sib is not None:
            if not isinstance(sib, Tag):
                sib = sib.next_sibling
                continue

            # Stop at next year section (h3) or at a strong-only <p> that
            # looks like a year number (for "Past Recipients" sub-sections)
            if sib.name == "h3":
                break
            if sib.name == "p":
                strong_child = sib.find("strong")
                if (
                    strong_child
                    and strong_child.get_text(strip=True).isdigit()
                    and len(strong_child.get_text(strip=True)) == 4
                ):
                    # This is a year marker for the next year
                    break

            # H4 = award category heading
            if sib.name in ("h4", "h5"):
                current_award_type = self._parse_aaai_award_type(
                    sib.get_text(strip=True), year,
                )
                sib = sib.next_sibling
                continue

            # <p> with <strong> = paper title
            if sib.name == "p":
                strong = sib.find("strong")
                if strong:
                    title = strong.get_text(strip=True)
                    author = self._extract_aaai_author(sib, strong)
                    if title:
                        items.append(self._make_award_item(
                            "AAAI", year, title, author, current_award_type,
                        ))
            sib = sib.next_sibling

        logger.info(
            "[ccf_bestpaper] Fetched %d AAAI %d award papers", len(items), year,
        )
        return items

    @staticmethod
    def _parse_aaai_award_type(heading: str, year: int) -> str:
        """Extract clean award type from AAAI heading text.

        E.g. "AAAI-26 Outstanding Paper Awards" -> "Outstanding Paper"
        """
        # Remove "AAAI-XX" prefix and trailing "Awards"/"Award"
        cleaned = re.sub(r"AAAI-\d+\s*", "", heading)
        cleaned = re.sub(r"\s*Awards?\s*$", "", cleaned)
        cleaned = re.sub(r"\s*–\s*", " - ", cleaned)
        return cleaned.strip() or "Outstanding Paper"

    @staticmethod
    def _extract_aaai_author(p_tag: Tag, strong_tag: Tag) -> str:
        """Extract author string from an AAAI paper <p> element.

        Handles two patterns:
        1) <p><strong>Title</strong><br>Authors</p>
        2) <p><strong>Title</strong></p> where authors are in the next <p>
        """
        # Pattern 1: check for <br> after strong, then text
        br = strong_tag.find_next_sibling("br")
        if br:
            # Text after <br> within same <p>
            texts = []
            node = br.next_sibling
            while node is not None:
                if isinstance(node, str):
                    texts.append(node.strip())
                elif isinstance(node, Tag):
                    texts.append(node.get_text(strip=True))
                node = node.next_sibling
            author = " ".join(t for t in texts if t)
            if author:
                return author

        # Pattern 2: text directly after <strong> in same <p>
        ns = strong_tag.next_sibling
        if isinstance(ns, str) and ns.strip():
            return ns.strip()

        # Pattern 3: author in the next <p> sibling (no <strong>)
        next_p = p_tag.find_next_sibling("p")
        if next_p and not next_p.find("strong"):
            return next_p.get_text(strip=True)

        return ""

    # ------------------------------------------------------------------
    # CVPR: per-year page at cvpr.thecvf.com/Conferences/{year}/BestPapersDemos
    # Structure: H2="Best Papers", then sequential <p> tags:
    #   <p>Award Type:</p>
    #   <p>Paper Name: TITLE</p>
    #   <p>Authors: AUTHOR LIST</p>
    #   <p><br/></p>  (separator)
    # ------------------------------------------------------------------

    async def _fetch_cvpr_awards(self, year: int) -> list[ContentItem]:
        """Scrape CVPR best papers page for a specific year."""
        url = CVPR_AWARDS_URL.format(year=year)
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True, headers=_HTTP_HEADERS,
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                logger.info(
                    "[ccf_bestpaper] CVPR %d awards page not found (404)", year,
                )
                return []
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[ContentItem] = []

        # Find the "Best Papers" h2 section
        h2 = soup.find("h2", string=lambda t: t and "Best Papers" in t)
        if not h2:
            logger.warning(
                "[ccf_bestpaper] CVPR %d: 'Best Papers' section not found", year,
            )
            return []

        # Walk <p> siblings collecting award entries
        current_award_type = "Best Paper"
        current_title = ""
        sib = h2.next_sibling

        while sib is not None:
            if not isinstance(sib, Tag):
                sib = sib.next_sibling
                continue

            # Stop at next major heading
            if sib.name in ("h1", "h2"):
                break

            if sib.name == "p":
                text = sib.get_text(strip=True)

                # Skip empty / br-only paragraphs
                if not text:
                    sib = sib.next_sibling
                    continue

                # Award type line (ends with ":")
                if re.match(
                    r"^(Best [\w\s]+|Honorable Mention)[:\s]*",
                    text, re.IGNORECASE,
                ) and "Paper Name" not in text and "Authors" not in text:
                    # Clean up: remove trailing ":", "ID: XXXX"
                    award_raw = re.sub(r"\s*:?\s*(ID:\s*\d+)?\s*$", "", text)
                    current_award_type = award_raw.strip()
                    sib = sib.next_sibling
                    continue

                # Paper name line
                if text.startswith("Paper Name:"):
                    current_title = text[len("Paper Name:"):].strip()
                    sib = sib.next_sibling
                    continue

                # Authors line
                if text.startswith("Authors:") and current_title:
                    author = text[len("Authors:"):].strip()
                    items.append(self._make_award_item(
                        "CVPR", year, current_title, author,
                        current_award_type, url,
                    ))
                    current_title = ""
                    sib = sib.next_sibling
                    continue

            sib = sib.next_sibling

        logger.info(
            "[ccf_bestpaper] Fetched %d CVPR %d award papers", len(items), year,
        )
        return items

    # ------------------------------------------------------------------
    # ACL: per-year page at {year}.aclweb.org/program/best_papers/
    # Structure: H2=category, <ul><li><strong>Title</strong><br><em>Authors</em>
    # Some <li> may also contain <u> for SAC sub-category
    # ------------------------------------------------------------------

    async def _fetch_acl_awards(self, year: int) -> list[ContentItem]:
        """Scrape ACL best papers page for a specific year."""
        url = ACL_AWARDS_URL.format(year=year)
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True, headers=_HTTP_HEADERS,
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                logger.info(
                    "[ccf_bestpaper] ACL %d awards page not found (404)", year,
                )
                return []
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[ContentItem] = []

        # Find the main content area
        content = (
            soup.find("section", class_="page__content")
            or soup.find("article")
            or soup.find("main")
            or soup
        )

        current_award_type = "Best Paper"

        for child in content.children:
            if not isinstance(child, Tag):
                continue

            # H2 = award category heading
            if child.name == "h2":
                heading = child.get_text(strip=True)
                # Clean up: "Best Paper Awards" -> "Best Paper"
                current_award_type = re.sub(
                    r"\s*Awards?\s*$", "", heading,
                ).strip()
                continue

            # <ul> contains paper entries as <li>
            if child.name == "ul":
                for li in child.find_all("li", recursive=False):
                    title, author = self._parse_acl_li(li)
                    if title:
                        items.append(self._make_award_item(
                            "ACL", year, title, author,
                            current_award_type, url,
                        ))

        logger.info(
            "[ccf_bestpaper] Fetched %d ACL %d award papers", len(items), year,
        )
        return items

    @staticmethod
    def _parse_acl_li(li: Tag) -> tuple[str, str]:
        """Parse an ACL <li> element to extract title and author.

        Expected format:
          <li><strong>Title</strong><br><em>Authors</em></li>
        Some entries also have <u>SAC Sub-category</u> before <em>.
        """
        strong = li.find("strong")
        if not strong:
            return "", ""
        title = strong.get_text(strip=True)

        # Remove trailing quote artifacts
        title = title.rstrip('"').rstrip('"')

        # Extract authors from <em>
        em = li.find("em")
        author = em.get_text(strip=True) if em else ""

        # Normalize semicolon-separated authors to commas
        if ";" in author:
            author = ", ".join(
                a.strip() for a in author.split(";") if a.strip()
            )

        return title, author
