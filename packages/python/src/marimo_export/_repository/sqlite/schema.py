from __future__ import annotations

import sqlite3


class IncompatibleRepositorySchema(RuntimeError):
    pass


def create_schema(
    connection: sqlite3.Connection,
    *,
    _validate: bool = True,
) -> None:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'repository_schema'"
    ).fetchone()
    if table is not None:
        version = connection.execute("SELECT version FROM repository_schema").fetchone()
        if version != (1,):
            raise IncompatibleRepositorySchema("The export repository schema is incompatible")
    _execute(
        connection,
        """
        CREATE TABLE IF NOT EXISTS repository_schema (
            version INTEGER PRIMARY KEY CHECK (version = 1)
        );
        INSERT OR IGNORE INTO repository_schema(version) VALUES (1);

        CREATE TABLE IF NOT EXISTS producers (
            producer_sha256 TEXT PRIMARY KEY,
            touched_at_us INTEGER NOT NULL,
            observation_revision INTEGER NOT NULL DEFAULT 0
                CHECK (observation_revision >= 0)
        );

        CREATE TABLE IF NOT EXISTS observations (
            producer_sha256 TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            input_names BLOB NOT NULL,
            values_json BLOB NOT NULL,
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            observed_order INTEGER NOT NULL CHECK (observed_order >= 1),
            PRIMARY KEY(producer_sha256, fingerprint),
            FOREIGN KEY(producer_sha256) REFERENCES producers(producer_sha256)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS observations_lru
            ON observations(producer_sha256, observed_order DESC, fingerprint);
        CREATE INDEX IF NOT EXISTS observations_relation_lru
            ON observations(
                producer_sha256, input_names, observed_order DESC, fingerprint
            );

        CREATE TABLE IF NOT EXISTS observation_events (
            producer_sha256 TEXT NOT NULL,
            observed_order INTEGER NOT NULL CHECK (observed_order >= 1),
            fingerprint TEXT NOT NULL,
            input_names BLOB NOT NULL,
            values_json BLOB NOT NULL,
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            PRIMARY KEY(producer_sha256, observed_order),
            FOREIGN KEY(producer_sha256) REFERENCES producers(producer_sha256)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS observation_events_relation_lru
            ON observation_events(
                producer_sha256, input_names, observed_order DESC
            );

        CREATE TABLE IF NOT EXISTS state_scopes (
            state_key TEXT PRIMARY KEY,
            producer_sha256 TEXT NOT NULL,
            output_plan_sha256 TEXT NOT NULL,
            state_fingerprint TEXT NOT NULL,
            current_instance TEXT,
            touched_at_us INTEGER NOT NULL,
            UNIQUE(producer_sha256, output_plan_sha256, state_fingerprint),
            FOREIGN KEY(producer_sha256) REFERENCES producers(producer_sha256)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS prepared_states (
            state_key TEXT NOT NULL,
            instance TEXT NOT NULL,
            metadata_json BLOB NOT NULL,
            metadata_bytes INTEGER NOT NULL CHECK (metadata_bytes >= 0),
            content_bytes INTEGER NOT NULL CHECK (content_bytes >= 0),
            created_at_us INTEGER NOT NULL,
            accessed_at_us INTEGER NOT NULL,
            PRIMARY KEY(state_key, instance),
            FOREIGN KEY(state_key) REFERENCES state_scopes(state_key)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS prepared_states_lru
            ON prepared_states(accessed_at_us DESC, created_at_us DESC, instance);

        CREATE TABLE IF NOT EXISTS identities (
            identity_key TEXT PRIMARY KEY,
            producer_sha256 TEXT NOT NULL,
            output_plan_sha256 TEXT NOT NULL,
            spec_sha256 TEXT NOT NULL,
            current_instance TEXT,
            touched_at_us INTEGER NOT NULL,
            UNIQUE(producer_sha256, output_plan_sha256, spec_sha256),
            FOREIGN KEY(producer_sha256) REFERENCES producers(producer_sha256)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS identities_lru
            ON identities(touched_at_us DESC, identity_key);

        CREATE TABLE IF NOT EXISTS generations (
            identity_key TEXT NOT NULL,
            instance TEXT NOT NULL,
            metadata_json BLOB NOT NULL,
            metadata_bytes INTEGER NOT NULL CHECK (metadata_bytes >= 0),
            captured_observation_revision INTEGER NOT NULL
                CHECK (captured_observation_revision >= 0),
            content_bytes INTEGER NOT NULL CHECK (content_bytes >= 0),
            created_at_us INTEGER NOT NULL,
            accessed_at_us INTEGER NOT NULL,
            PRIMARY KEY(identity_key, instance),
            FOREIGN KEY(identity_key) REFERENCES identities(identity_key)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS generations_lru
            ON generations(accessed_at_us DESC, created_at_us DESC, instance);

        CREATE TABLE IF NOT EXISTS generation_states (
            identity_key TEXT NOT NULL,
            generation_instance TEXT NOT NULL,
            state_key TEXT NOT NULL,
            state_instance TEXT NOT NULL,
            PRIMARY KEY(identity_key, generation_instance, state_key),
            FOREIGN KEY(identity_key, generation_instance)
                REFERENCES generations(identity_key, instance) ON DELETE CASCADE,
            FOREIGN KEY(state_key, state_instance)
                REFERENCES prepared_states(state_key, instance) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS artifact_leases (
            owner TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('generation', 'state')),
            artifact_key TEXT NOT NULL,
            instance TEXT NOT NULL,
            expires_at_us INTEGER NOT NULL,
            PRIMARY KEY(owner, kind, artifact_key, instance)
        );
        CREATE INDEX IF NOT EXISTS artifact_leases_expiry
            ON artifact_leases(expires_at_us, kind, artifact_key, instance);

        CREATE TABLE IF NOT EXISTS staging_leases (
            owner TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            expires_at_us INTEGER NOT NULL,
            PRIMARY KEY(owner, relative_path)
        );
        CREATE INDEX IF NOT EXISTS staging_leases_expiry
            ON staging_leases(expires_at_us, relative_path);

        CREATE TABLE IF NOT EXISTS preparation_reservations (
            identity_key TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            fence INTEGER NOT NULL CHECK (fence >= 1),
            producer_sha256 TEXT NOT NULL,
            output_plan_sha256 TEXT NOT NULL,
            spec_sha256 TEXT NOT NULL,
            expires_at_us INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS preparation_reservations_expiry
            ON preparation_reservations(expires_at_us, identity_key);

        CREATE TABLE IF NOT EXISTS retired_artifacts (
            relative_path TEXT PRIMARY KEY,
            content_bytes INTEGER NOT NULL CHECK (content_bytes >= 0),
            created_at_us INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS repository_counters (
            name TEXT PRIMARY KEY,
            value INTEGER NOT NULL CHECK (value >= 0)
        );
        INSERT OR IGNORE INTO repository_counters(name, value)
        VALUES ('preparation_fence', 0);
        """,
    )
    if _validate:
        _validate_shape(connection)


def _validate_shape(connection: sqlite3.Connection) -> None:
    expected_columns = {
        "repository_schema": ("version",),
        "producers": ("producer_sha256", "touched_at_us", "observation_revision"),
        "observations": (
            "producer_sha256",
            "fingerprint",
            "input_names",
            "values_json",
            "byte_count",
            "observed_order",
        ),
        "observation_events": (
            "producer_sha256",
            "observed_order",
            "fingerprint",
            "input_names",
            "values_json",
            "byte_count",
        ),
        "state_scopes": (
            "state_key",
            "producer_sha256",
            "output_plan_sha256",
            "state_fingerprint",
            "current_instance",
            "touched_at_us",
        ),
        "prepared_states": (
            "state_key",
            "instance",
            "metadata_json",
            "metadata_bytes",
            "content_bytes",
            "created_at_us",
            "accessed_at_us",
        ),
        "identities": (
            "identity_key",
            "producer_sha256",
            "output_plan_sha256",
            "spec_sha256",
            "current_instance",
            "touched_at_us",
        ),
        "generations": (
            "identity_key",
            "instance",
            "metadata_json",
            "metadata_bytes",
            "captured_observation_revision",
            "content_bytes",
            "created_at_us",
            "accessed_at_us",
        ),
        "generation_states": (
            "identity_key",
            "generation_instance",
            "state_key",
            "state_instance",
        ),
        "artifact_leases": (
            "owner",
            "kind",
            "artifact_key",
            "instance",
            "expires_at_us",
        ),
        "staging_leases": ("owner", "relative_path", "expires_at_us"),
        "preparation_reservations": (
            "identity_key",
            "owner",
            "fence",
            "producer_sha256",
            "output_plan_sha256",
            "spec_sha256",
            "expires_at_us",
        ),
        "retired_artifacts": ("relative_path", "content_bytes", "created_at_us"),
        "repository_counters": ("name", "value"),
    }
    for table, expected in expected_columns.items():
        actual = tuple(str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")'))
        if actual != expected:
            raise IncompatibleRepositorySchema(
                f"The export repository table {table!r} is incompatible"
            )
    expected_indexes = {
        "observations_lru",
        "observations_relation_lru",
        "observation_events_relation_lru",
        "prepared_states_lru",
        "identities_lru",
        "generations_lru",
        "artifact_leases_expiry",
        "staging_leases_expiry",
        "preparation_reservations_expiry",
    }
    actual_indexes = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'index' AND name NOT LIKE 'sqlite_autoindex_%'
            """
        )
    }
    if actual_indexes != expected_indexes:
        raise IncompatibleRepositorySchema("The export repository indexes are incompatible")
    reference = sqlite3.connect(":memory:", isolation_level=None)
    try:
        reference.execute("PRAGMA foreign_keys = ON")
        create_schema(reference, _validate=False)
        expected_definition = _schema_definition(reference)
    finally:
        reference.close()
    if _schema_definition(connection) != expected_definition:
        raise IncompatibleRepositorySchema("The export repository constraints are incompatible")


def _schema_definition(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            " ".join(str(row[3]).split()),
        )
        for row in connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_autoindex_%'
            ORDER BY type, name
            """
        )
    )


def _execute(connection: sqlite3.Connection, script: str) -> None:
    statement = ""
    for line in script.splitlines():
        statement += f"{line}\n"
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise RuntimeError("The export repository schema is incomplete")


__all__ = ["IncompatibleRepositorySchema", "create_schema"]
