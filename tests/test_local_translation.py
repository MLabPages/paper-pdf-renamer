import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from paper_pdf_renamer.config import Settings
from paper_pdf_renamer.gui import AppState
from paper_pdf_renamer.local_translation import save_translation, translate_pdf
from paper_pdf_renamer.models import ResolvedMetadata
from paper_pdf_renamer.operations import RenameService


@pytest.mark.parametrize(("enabled", "translated"), [(True, False), (False, False), (True, True)])
def test_translate_only_approved_originals(tmp_path, enabled, translated):
    state = AppState(Settings(history_dir=str(tmp_path / "logs"), auto_translate=enabled))
    metadata = ResolvedMetadata("10.1234/example", "Safe Paper", ("Smith",), 2020, "en",
                                "crossref:doi", 0.99, translated=translated)
    service = RenameService(lambda _: metadata, history=state.history)
    source = tmp_path / "download.pdf"
    source.write_bytes(b"%PDF-original")
    candidate = service.make_candidate(source)
    state._upsert(candidate)
    with (
        patch.object(state, "_service", return_value=service),
        patch("paper_pdf_renamer.gui.translate_pdf", return_value=b"%PDF-translated") as translate,
        patch.object(state.watch_manifest, "complete"),
    ):
        assert not translate.called
        assert state.apply([next(iter(state._candidates))]) == 1
        worker = state._translation_worker
        if worker:
            worker.join(5)
        assert not worker or not worker.is_alive()
        assert translate.call_count == int(enabled and not translated)
    if enabled and not translated:
        destination = candidate.destination_path
        assert destination.read_bytes() == b"%PDF-original"
        output = destination.with_name("[ja] [PLaMo] " + destination.name)
        assert output.read_bytes() == b"%PDF-translated"
        assert not save_translation(output, b"%PDF-other")
        assert output.read_bytes() == b"%PDF-translated"


def test_reader_uses_fixed_engine_and_fetches_translated_pdf(tmp_path):
    source = tmp_path / "論文.pdf"
    source.write_bytes(b"%PDF-original")
    doc_id = "abcdef012345"
    results = [json.dumps({"id": doc_id}).encode(),
               b'{"translations":{"plamo-0.8b":{"status":"done"}}}', b"%PDF-translated"]
    with (
        patch("paper_pdf_renamer.local_translation.reader_connection", return_value=(8765, "test")),
        patch("paper_pdf_renamer.local_translation._request", side_effect=results) as request,
    ):
        assert translate_pdf(source) == b"%PDF-translated"
    assert "engine=plamo-0.8b" in request.call_args_list[0].args[2]
    assert request.call_args_list[0].args[3] == b"%PDF-original"
    assert request.call_args.args[2] == f"/api/doc/{doc_id}/translated/plamo-0.8b"


def test_history_can_translate_without_renaming_again(tmp_path):
    state = AppState(Settings(history_dir=str(tmp_path / "logs")))
    metadata = ResolvedMetadata("10.1234/example", "Safe Paper", ("Smith",), 2020, "en",
                                "crossref:doi", 0.99)
    service = RenameService(lambda _: metadata, history=state.history)
    source = tmp_path / "download.pdf"
    source.write_bytes(b"%PDF-original")
    renamed = service.process(source).destination_path
    row = state.snapshot()["history"][-1]
    assert row["can_translate"]
    with pytest.raises(ValueError):
        state.translate_from_history(str(source), row["timestamp"])
    with patch("paper_pdf_renamer.gui.translate_pdf", return_value=b"%PDF-translated"):
        assert state.translate_from_history(str(renamed), row["timestamp"])
        worker = state._translation_worker
        if worker:
            worker.join(5)
    output = renamed.with_name("[ja] [PLaMo] " + renamed.name)
    assert output.read_bytes() == b"%PDF-translated"
    assert not state.snapshot()["history"][-1]["can_translate"]
    assert not state.translate_from_history(str(renamed), row["timestamp"])
    assert state.undo()["status"] == "undone"
    with pytest.raises(ValueError):
        state.translate_from_history(str(renamed), row["timestamp"])
