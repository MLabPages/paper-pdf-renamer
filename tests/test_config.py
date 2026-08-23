from pathlib import Path

from paper_pdf_renamer.config import FORMAT_TEMPLATE, Settings


def test_settings_round_trip_and_safe_bounds(tmp_path: Path):
    path = tmp_path / "settings.json"
    settings = Settings(
        watch_folders=[str(tmp_path)],
        format_template=FORMAT_TEMPLATE,
        max_title_length=999,
        min_confidence=0.2,
        poll_interval=999,
    ).validate()
    settings.save(path)

    loaded = Settings.load(path)
    assert loaded.watch_folders == [str(tmp_path)]
    assert loaded.max_title_length == 200
    assert loaded.min_confidence == 0.90
    assert loaded.poll_interval == 60.0


def test_legacy_format_template_is_migrated():
    settings = Settings(format_template="著者_出版年_論文タイトル.pdf").validate()
    assert settings.format_template == FORMAT_TEMPLATE
