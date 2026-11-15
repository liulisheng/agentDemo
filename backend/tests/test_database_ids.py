from agent_platform.domain.common import generate_database_id


def test_database_id_fits_char26_and_is_unambiguous_opaque_value() -> None:
    identifiers = {generate_database_id() for _ in range(100)}

    assert len(identifiers) == 100
    assert all(len(identifier) == 26 for identifier in identifiers)
    assert all(set(identifier) <= set("abcdefghijklmnopqrstuvwxyz234567") for identifier in identifiers)
