import json
from unittest import mock

import pytest

from openhealth import provider_response


def _binding(
    message_id=10,
    attempt_message_id=None,
    request_sha256="b" * 64,
):
    return provider_response.response_binding(
        provider="synthetic",
        requested_model="model-synthetic",
        source_message_id=message_id,
        attempt_message_id=attempt_message_id,
        photo_artifact_id=f"artifact-photo-{message_id}",
        photo_sha256="a" * 64,
        request_sha256=request_sha256,
    )


def test_archive_preserves_exact_bytes_and_binding(tmp_path):
    path = tmp_path / "response.provider.json"
    raw_body = b'{"content":[{"type":"text","text":"{\\"mode\\":\\"x\\"}"}]}'

    replay_body, wrapper = provider_response.archive_response(
        path,
        raw_body,
        binding=_binding(),
        http_status=200,
        content_type="application/json",
        received_at="2026-07-27T12:00:00+00:00",
    )

    assert replay_body == raw_body
    assert wrapper["binding"] == _binding()
    assert wrapper["response_body_sha256"] == (
        provider_response.sha256_bytes(raw_body)
    )
    assert path.stat().st_mode & 0o777 == 0o400
    archive_text = path.read_text(encoding="utf-8")
    assert "SECRET_KEY_SENTINEL" not in archive_text
    assert "PROMPT_SENTINEL" not in archive_text


def test_existing_archive_replays_without_overwrite(tmp_path):
    path = tmp_path / "response.provider.json"
    raw_body = b'{"content":[]}'
    provider_response.archive_response(
        path,
        raw_body,
        binding=_binding(),
        http_status=200,
        content_type="application/json",
        received_at="2026-07-27T12:00:00+00:00",
    )
    first_bytes = path.read_bytes()

    replay_body, wrapper = provider_response.archive_response(
        path,
        raw_body,
        binding=_binding(),
        http_status=200,
        content_type="application/json",
    )

    assert replay_body == raw_body
    assert wrapper["received_at"] == "2026-07-27T12:00:00+00:00"
    assert path.read_bytes() == first_bytes


def test_existing_archive_rejects_different_body_without_overwrite(tmp_path):
    path = tmp_path / "response.provider.json"
    provider_response.archive_response(
        path,
        b'{"content":[]}',
        binding=_binding(),
        http_status=200,
        content_type="application/json",
    )
    first_bytes = path.read_bytes()

    with pytest.raises(provider_response.ProviderResponseError) as error:
        provider_response.archive_response(
            path,
            b"foreign response that must not overwrite",
            binding=_binding(),
            http_status=200,
            content_type="application/json",
        )

    assert error.value.code == "provider_response_archive_invalid"
    assert path.read_bytes() == first_bytes


def test_foreign_binding_is_rejected_without_overwrite(tmp_path):
    path = tmp_path / "response.provider.json"
    provider_response.archive_response(
        path,
        b'{"content":[]}',
        binding=_binding(10),
        http_status=200,
        content_type="application/json",
    )
    first_bytes = path.read_bytes()

    with pytest.raises(provider_response.ProviderResponseError) as error:
        provider_response.load_response_archive(path, _binding(11))

    assert error.value.code == "provider_response_archive_invalid"
    assert error.value.stage == "provider_archive"
    assert path.read_bytes() == first_bytes


def test_changed_request_hash_is_rejected_without_reusing_response(tmp_path):
    path = tmp_path / "response.provider.json"
    provider_response.archive_response(
        path,
        b'{"content":[]}',
        binding=_binding(request_sha256="b" * 64),
        http_status=200,
        content_type="application/json",
    )

    with pytest.raises(provider_response.ProviderResponseError) as error:
        provider_response.recover_response_archive(
            path,
            _binding(request_sha256="c" * 64),
        )

    assert error.value.code == "provider_response_archive_invalid"


def test_corrupt_or_symlinked_archive_is_rejected(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "response.provider.json"
    link.symlink_to(target)

    with pytest.raises(provider_response.ProviderResponseError) as error:
        provider_response.load_response_archive(link, _binding())

    assert error.value.code == "provider_response_archive_invalid"


def test_archive_failure_after_response_is_terminal(tmp_path):
    path = tmp_path / "response.provider.json"
    with mock.patch.object(
        provider_response.os,
        "open",
        side_effect=PermissionError("synthetic"),
    ), pytest.raises(provider_response.ProviderResponseError) as error:
        provider_response.archive_response(
            path,
            b'{"content":[]}',
            binding=_binding(),
            http_status=200,
            content_type="application/json",
        )

    assert error.value.code == "provider_response_archive_failed"
    assert error.value.stage == "provider_archive"


def test_fsynced_pending_archive_recovers_without_new_response(tmp_path):
    path = tmp_path / "response.provider.json"
    raw_body = b'{"content":[{"type":"text","text":"ok"}]}'
    with mock.patch.object(
        provider_response.os,
        "link",
        side_effect=SystemExit("synthetic pre-publish crash"),
    ), pytest.raises(SystemExit):
        provider_response.archive_response(
            path,
            raw_body,
            binding=_binding(),
            http_status=200,
            content_type="application/json",
        )

    staging = list(tmp_path.glob(".response.provider.json.*.tmp"))
    assert not path.exists()
    assert len(staging) == 1
    assert staging[0].stat().st_mode & 0o777 == 0o400

    recovered = provider_response.recover_response_archive(
        path,
        _binding(),
    )

    assert recovered is not None
    assert recovered[0] == raw_body
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o400
    assert not staging[0].exists()


def test_partial_staging_fails_closed_before_another_provider_call(tmp_path):
    path = tmp_path / "response.provider.json"
    staging = tmp_path / ".response.provider.json.synthetic.tmp"
    staging.write_bytes(b'{"schema_version":1')
    staging.chmod(0o400)

    with pytest.raises(provider_response.ProviderResponseError) as error:
        provider_response.recover_response_archive(
            path,
            _binding(),
        )

    assert error.value.code == "provider_response_archive_incomplete"
    assert error.value.stage == "provider_archive"
    assert not path.exists()
    assert staging.read_bytes() == b'{"schema_version":1'


def test_extract_text_skips_leading_non_text_blocks():
    envelope = {
        "content": [
            {"type": "thinking", "thinking": "private reasoning"},
            {"type": "tool_use", "name": "synthetic"},
            {"type": "text", "text": '{"mode":"nutrition_label"}'},
        ]
    }

    parsed, text = provider_response.extract_text(
        json.dumps(envelope).encode()
    )

    assert parsed == envelope
    assert text == '{"mode":"nutrition_label"}'
    assert "private reasoning" not in text


@pytest.mark.parametrize(
    ("raw_body", "code", "stage"),
    [
        (
            b"not-json",
            "provider_envelope_json_invalid",
            "provider_envelope",
        ),
        (
            b"[]",
            "provider_envelope_json_invalid",
            "provider_envelope",
        ),
        (
            b'{"content":null}',
            "provider_text_block_missing",
            "provider_text",
        ),
        (
            b'{"content":[{"type":"thinking","thinking":"x"}]}',
            "provider_text_block_missing",
            "provider_text",
        ),
    ],
)
def test_extract_text_fails_with_bounded_codes(raw_body, code, stage):
    with pytest.raises(provider_response.ProviderResponseError) as error:
        provider_response.extract_text(raw_body)

    assert error.value.code == code
    assert error.value.stage == stage


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("not-json", "provider_payload_json_invalid"),
        ("[]", "provider_payload_schema_invalid"),
    ],
)
def test_extract_json_object_fails_closed(text, code):
    with pytest.raises(provider_response.ProviderResponseError) as error:
        provider_response.extract_json_object(text)

    assert error.value.code == code
    assert error.value.stage == "provider_payload"


def test_extract_json_object_accepts_unadorned_or_fenced_text():
    assert provider_response.extract_json_object('{"mode":"not_food"}') == {
        "mode": "not_food"
    }
    assert provider_response.extract_json_object(
        'prefix\n```json\n{"mode":"not_food"}\n```\nsuffix'
    ) == {"mode": "not_food"}
