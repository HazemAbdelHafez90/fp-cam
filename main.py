#!/usr/bin/env python3
"""
Canto consent matcher CLI

Usage:
    python main.py --project 1319          # find matches for project 1319
    python main.py --project 1319 --link   # match + write Consent field to Canto
    python main.py --image <asset_id>      # find consent for a single image
"""

import argparse
import sys
import os
from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm

import canto_client as canto
from pdf_parser import parse_consent_pdf
from matcher import score_match

console = Console()

SCORE_AUTO   = 85   # auto-confirm
SCORE_REVIEW = 60   # ask user


def load_pdf_data(doc: dict) -> dict | None:
    """Download (or load from cache) and parse a consent PDF."""
    url = doc.get("url", {}).get("directUrlOriginal", "")
    if not url:
        return None
    try:
        raw = canto.download_pdf_cached(doc["id"], url)
        data = parse_consent_pdf(raw)
        data["_id"] = doc["id"]
        data["_name"] = doc.get("name", "")
        data["_url"] = doc.get("url", {}).get("detail", "")
        return data
    except Exception as e:
        console.print(f"  [yellow]Warning: could not read {doc.get('name')} — {e}[/yellow]")
        return None


def run_project(project_id: str, do_link: bool):
    console.print(f"\n[bold cyan]Project:[/bold cyan] {project_id}")

    console.print("  Fetching images...")
    images = canto.get_project_images(project_id)
    console.print(f"  Found [green]{len(images)} images[/green]")

    console.print("  Fetching consent PDFs...")
    docs = canto.get_project_documents(project_id)
    console.print(f"  Found [yellow]{len(docs)} documents[/yellow]")

    if not images or not docs:
        console.print("[red]Need at least one image and one PDF.[/red]")
        sys.exit(1)

    # Parse all PDFs
    console.print("\n[bold]Reading PDF content...[/bold]")
    pdf_data_list = []
    for doc in docs:
        if not doc.get("name", "").lower().endswith(".pdf"):
            continue
        data = load_pdf_data(doc)
        if data:
            names = ", ".join(data["all_names"]) or "(no name)"
            console.print(f"  [dim]{doc['name']}[/dim] → [cyan]{names}[/cyan]")
            pdf_data_list.append(data)

    if not pdf_data_list:
        console.print("[red]No readable PDFs found.[/red]")
        sys.exit(1)

    # Score every image × PDF
    console.print("\n[bold]Scoring matches...[/bold]")
    matches = []
    for image in images:
        best_result, best_pdf = None, None
        for pdf_data in pdf_data_list:
            result = score_match(image, pdf_data)
            if best_result is None or result.overall > best_result.overall:
                best_result, best_pdf = result, pdf_data
        matches.append({
            "image": image,
            "pdf": best_pdf,
            "result": best_result,
        })

    matches.sort(key=lambda m: m["result"].overall, reverse=True)

    # Display results
    table = Table(title=f"Matches — Project {project_id}", show_lines=True)
    table.add_column("Score", width=7)
    table.add_column("Tier", width=8)
    table.add_column("Image", style="green")
    table.add_column("Person Shown", style="cyan")
    table.add_column("Consent PDF", style="yellow")
    table.add_column("Signals (name / project / location)")

    for m in matches:
        result = m["result"]
        score = result.overall
        img_name = m["image"].get("name", "?")
        person = m["image"].get("additional", {}).get("Person Shown in the Image") or "—"
        pdf_name = m["pdf"]["_name"] if m["pdf"] else "—"

        # Signal breakdown: N=xx P=xx L=xx
        if result.signals:
            sig_map = {s.signal: s.score for s in result.signals}
            signals_str = (
                f"N={sig_map.get('name', 0):.0f} "
                f"P={sig_map.get('project', 0):.0f} "
                f"L={sig_map.get('location', 0):.0f}"
            )
        else:
            signals_str = "—"

        tier_display = {
            "auto":   "[green]AUTO[/green]",
            "review": "[yellow]REVIEW[/yellow]",
            "skip":   "[red]SKIP[/red]",
        }.get(result.tier, "—")

        table.add_row(f"{score:.0f}", tier_display, img_name, person, pdf_name, signals_str)

    console.print(table)

    if not do_link:
        console.print("\n[dim]Run with --link to write matches back to Canto.[/dim]")
        return

    # Confirm and link
    to_link = [m for m in matches if m["result"].tier == "auto" and m["pdf"]]
    to_review = [m for m in matches if m["result"].tier == "review" and m["pdf"]]

    if to_review:
        console.print(f"\n[yellow]{len(to_review)} matches need review:[/yellow]")
        for m in to_review:
            img = m["image"].get("name", m["image"]["id"])
            pdf = m["pdf"]["_name"]
            score = m["result"].overall
            if Confirm.ask(f"  Link [green]{img}[/green] ↔ [yellow]{pdf}[/yellow] (score {score:.0f})?"):
                to_link.append(m)

    console.print(f"\n[bold]Linking {len(to_link)} pairs...[/bold]")
    ok = fail = 0
    for m in to_link:
        img = m["image"]
        try:
            success = canto.update_consent_field(img["id"], m["pdf"]["_id"])
            status = "[green]✓[/green]" if success else "[red]✗[/red]"
            console.print(f"  {status} {img.get('name')} ↔ {m['pdf']['_name']}")
            if success:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            console.print(f"  [red]✗[/red] {img.get('name')} — {e}")
            fail += 1

    console.print(f"\n[bold]Done:[/bold] {ok} linked, {fail} failed.")


def run_single_image(image_id: str, do_link: bool):
    console.print(f"\n[bold cyan]Looking up image:[/bold cyan] {image_id}")
    image = canto.get_asset("image", image_id)
    person = image.get("additional", {}).get("Person Shown in the Image")
    albums = [a["namePath"] for a in image.get("relatedAlbums", [])]

    console.print(f"  Name: {image.get('name')}")
    console.print(f"  Person Shown: [cyan]{person or '(empty)'}[/cyan]")
    console.print(f"  Albums: {albums}")

    # Extract project ID from album path (e.g. "..._1319_Madagascar/...")
    import re
    project_ids = re.findall(r"_(\d{3,4})_", " ".join(albums))
    if not project_ids:
        console.print("[red]Could not determine project ID from album path.[/red]")
        sys.exit(1)

    project_id = project_ids[0]
    console.print(f"  Project ID: [bold]{project_id}[/bold]")

    run_project(project_id, do_link)


if __name__ == "__main__":
    # Set token from env or prompt
    if not canto.TOKEN:
        canto.TOKEN = os.environ.get("CANTO_TOKEN", "")

    parser = argparse.ArgumentParser(description="Canto consent matcher")
    parser.add_argument("--project", help="Project ID to process (e.g. 1319)")
    parser.add_argument("--image", help="Single image asset ID")
    parser.add_argument("--link", action="store_true", help="Write matches back to Canto")
    args = parser.parse_args()

    if args.project:
        run_project(args.project, args.link)
    elif args.image:
        run_single_image(args.image, args.link)
    else:
        parser.print_help()
