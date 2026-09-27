import pytest

from api.services.humain_voice_catalog import _normalize


def test_humain_voice_catalog_maps_profile_ids_to_descriptive_options():
    assert _normalize(
        {
            "id": "profile-42",
            "label": "Maya",
            "profile": {
                "speaker": {"gender": "female", "dialect": "British"},
                "languages": ["en", "ar"],
            },
        }
    ) == {
        "voice_id": "profile-42",
        "name": "Maya",
        "description": "British · en, ar",
        "gender": "female",
        "accent": "British",
        "language": "en, ar",
    }


def test_humain_voice_catalog_uses_id_when_profile_has_no_label():
    assert _normalize({"id": "profile-42"})["name"] == "profile-42"
