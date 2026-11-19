#!/usr/bin/env python3
"""Scrape RTE Player 'On Now' -> rte.m3u + rte.xml.gz (alphabetical by channel)."""
import gzip, json, re, sys
from datetime import datetime, timezone
from xml.sax.saxutils import escape
from playwright.sync_api import sync_playwright

URL = "https://www.rte.ie/player/onnow"
DEBUG = "--debug" in sys.argv  # dumps captured JSON to debug_responses.json

NAME_K = ("channelName", "channelTitle", "stationName", "name")
ID_K = ("channelId", "id", "slug", "stationId")
LOGO_K = ("logo", "logoUrl", "image", "channelLogo")
STREAM_K = ("streamUrl", "stream", "url", "link")
TITLE_K = ("title", "programmeTitle", "programTitle", "name")
DESC_K = ("description", "synopsis", "shortDescription")
START_K = ("start", "startTime", "startDate", "start_time")
END_K = ("end", "endTime", "endDate", "end_time")


def first(d, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", {}, []):
            return v
    return None


def to_dt(v):
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, timezone.utc)
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def extract(data):
    channels, progs = {}, []

    def visit(node, ch):
        if isinstance(node, list):
            for i in node:
                visit(i, ch)
        elif isinstance(node, dict):
            s, e = to_dt(first(node, START_K)), to_dt(first(node, END_K))
            title = first(node, TITLE_K)
            if s and e and isinstance(title, str):
                cname = first(node, ("channelName", "channelTitle", "stationName"))
                cid = ch["id"] if ch else (re.sub(r"\W+", "-", cname.lower()) if cname else None)
                if cid:
                    if not ch:
                        channels.setdefault(cid, {"id": cid, "name": cname, "logo": "", "url": ""})
                    d = first(node, DESC_K)
                    progs.append({"ch": cid, "title": title, "start": s, "end": e,
                                  "desc": d if isinstance(d, str) else ""})
            else:
                name = first(node, NAME_K)
                if isinstance(name, str) and any(k in node for k in ID_K + LOGO_K + STREAM_K):
                    cid = str(first(node, ID_K) or re.sub(r"\W+", "-", name.lower()))
                    logo, url = first(node, LOGO_K), first(node, STREAM_K)
                    ch = channels.setdefault(cid, {"id": cid, "name": name, "logo": "", "url": ""})
                    if isinstance(logo, str): ch["logo"] = logo
                    if isinstance(url, str) and url.startswith("http"): ch["url"] = url
            for v in node.values():
                visit(v, ch)

    visit(data, None)
    return channels, progs


def fetch():
    captured = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(locale="en-IE", timezone_id="Europe/Dublin").new_page()

        def on_resp(r):
            if "json" in (r.headers.get("content-type") or ""):
                try:
                    captured.append({"url": r.url, "data": r.json()})
                except Exception:
                    pass

        page.on("response", on_resp)
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        b.close()
    if DEBUG:
        json.dump(captured, open("debug_responses.json", "w"), indent=1)
    return captured


def main():
    channels, progs = {}, []
    for r in fetch():
        c, p = extract(r["data"])
        for k, v in c.items():
            channels.setdefault(k, v).update({a: b for a, b in v.items() if b})
        progs += p
    if not progs and not channels:
        sys.exit("No channel data captured - run with --debug and inspect debug_responses.json")

    chans = sorted(channels.values(), key=lambda c: (c["name"] or c["id"]).lower())
    progs.sort(key=lambda p: (channels[p["ch"]]["name"] or p["ch"]).lower() + p["start"].isoformat())

    with open("rte.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for c in chans:
            name = c["name"] or c["id"]
            url = c["url"] or f"https://www.rte.ie/player/live/{c['id']}"
            f.write(f'#EXTINF:-1 tvg-id="{escape(c["id"])}" tvg-name="{escape(name)}" '
                    f'tvg-logo="{escape(c["logo"])}" group-title="RTE",{name}\n{url}\n')

    fmt = lambda d: d.strftime("%Y%m%d%H%M%S +0000")
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="rte-scraper">']
    for c in chans:
        out.append(f'<channel id="{escape(c["id"])}"><display-name>{escape(c["name"] or c["id"])}'
                   f'</display-name></channel>')
    for p in progs:
        out.append(f'<programme start="{fmt(p["start"])}" stop="{fmt(p["end"])}" channel="{escape(p["ch"])}">'
                   f'<title>{escape(p["title"])}</title>'
                   + (f'<desc>{escape(p["desc"])}</desc>' if p["desc"] else "") + "</programme>")
    out.append("</tv>")
    with gzip.open("rte.xml.gz", "wt", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"{len(chans)} channels, {len(progs)} programmes")


if __name__ == "__main__":
    main()
