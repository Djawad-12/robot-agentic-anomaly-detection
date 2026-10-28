"""Downloads the official Universal Robots error code pages into one text file for `search_docs`.

The pages are public (universal-robots.com/manuals). Every code keeps the web address of its
page, so that the agent and the fault catalogue can quote it as proof. Codes up to 256 apply
to every robot generation, codes above 256 only to e-Series and later models (as stated on
the index page): both lists are fetched.

Usage: python -m tools.fetch_ur_docs [--version SW5_26]
Writes `manuals/ursim/ur_error_codes_<version>.md`.
"""

from __future__ import annotations

import argparse
import html
import logging
import re
import time
import urllib.request

from robot_agentic.config import load_settings, setup_logging

log = logging.getLogger(__name__)

BASE = "https://www.universal-robots.com/manuals/EN/HTML/{version}/Content/prod-err-codes/"
INDEXES = ["topics/CODE_0.html", "err-LegacyErrorCodes.htm"]


def _get(url: str) -> str:
    """Download a page as text.

    Raises:
        urllib.error.URLError: if the page cannot be downloaded.
    """
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "robot-agentic"}), timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _code_page(page: str) -> tuple[str, list[str]]:
    """Title and content lines of a code page.

    The page holds a title (`<h1>`), then each sub-code (`<p class="variable">C204A3 Sudden
    stop</p>`) followed by its explanation and suggestion, if any. The generic "contact
    support" note is left out.
    """
    clean = lambda s: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()  # noqa: E731
    title = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", page)
    start = title.end() if title else 0
    end = page.find('<div class="clearfix"', start)
    content = re.sub(r'(?is)<div[^>]*class="tech_note".*?</div>', "", page[start : end if end > 0 else None])
    prefix = {"variable": "", "explanation": "  Explanation: ", "suggestion": "  Suggestion: "}
    lines = [prefix.get(kind, "") + clean(text) for kind, text in re.findall(r'(?is)<p[^>]*class="(\w+)"[^>]*>(.*?)</p>', content)]
    return (clean(title.group(1)) if title else ""), [x for x in lines if x.strip()]


def main() -> None:
    """Fetch the index pages, then every code page, and write them as one Markdown file."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="SW5_26", help="software version folder of the online manual")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings)
    base = BASE.format(version=args.version)

    pages: dict[int, str] = {}
    for index in INDEXES:
        for href in re.findall(r'href="((?:topics/)?CODE_\d+\.html)"', _get(base + index)):
            pages.setdefault(int(re.search(r"\d+", href).group()), base + ("" if href.startswith("topics/") else "topics/") + href)
    sections = [f"# Universal Robots error codes ({args.version})\n\nSource: {base}err-LegacyErrorCodes.htm\n"]
    for number in sorted(pages):
        url = pages[number]
        title, lines = _code_page(_get(url))
        sections.append(f"## {title or f'C{number}'}\n\nSource: {url}\n\n" + "\n".join(lines))
        log.info("fetched C%d", number)
        time.sleep(0.2)
    out = settings.manuals_dir / "ursim" / f"ur_error_codes_{args.version}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n".join(sections) + "\n")
    print(f"{len(pages)} codes written to {out}")


if __name__ == "__main__":
    main()
