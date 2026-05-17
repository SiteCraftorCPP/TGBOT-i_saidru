import asyncio
from pathlib import Path

from app.core.config import Settings


class PdfGenerationError(RuntimeError):
    pass


async def convert_docx_to_pdf(docx_path: Path, output_dir: Path, settings: Settings) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        settings.libreoffice_path,
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(docx_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        message = (stderr or stdout).decode("utf-8", errors="ignore").strip()
        raise PdfGenerationError(message or "LibreOffice не смог создать PDF")
    pdf_path = output_dir / docx_path.with_suffix(".pdf").name
    return pdf_path if pdf_path.exists() else None
