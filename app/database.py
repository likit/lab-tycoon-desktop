"""Application-level LIS database configuration and connection management."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine


SUPPORTED_DATABASE_DRIVERS = ("sqlite",)


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Serializable settings describing how the LIS reaches its database."""

    driver: str = "sqlite"
    database_path: str = ""

    @classmethod
    def default_sqlite(cls, database_path: str | Path) -> "DatabaseConfig":
        return cls(driver="sqlite", database_path=str(Path(database_path).expanduser().resolve()))

    @classmethod
    def from_dict(
        cls,
        payload: object,
        *,
        default_database_path: str | Path,
    ) -> "DatabaseConfig":
        if not isinstance(payload, dict):
            return cls.default_sqlite(default_database_path)
        return cls(
            driver=str(payload.get("driver", "sqlite")).lower(),
            database_path=str(payload.get("database_path") or default_database_path),
        ).normalized()

    def normalized(self) -> "DatabaseConfig":
        return DatabaseConfig(
            driver=self.driver.strip().lower(),
            database_path=str(Path(self.database_path).expanduser().resolve()) if self.database_path else "",
        )

    def to_dict(self) -> dict[str, str]:
        normalized = self.normalized()
        return {
            "driver": normalized.driver,
            "database_path": normalized.database_path,
        }


class DatabaseConfigurationError(ValueError):
    """Raised when LIS database settings cannot be used safely."""


class DatabaseManager:
    """Central runtime factory for the configured LIS database."""

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config.normalized()

    def validate(self, *, require_existing_database: bool = False) -> None:
        if self.config.driver not in SUPPORTED_DATABASE_DRIVERS:
            raise DatabaseConfigurationError(
                f"Unsupported database driver: {self.config.driver or '(empty)'}"
            )
        if not self.config.database_path:
            raise DatabaseConfigurationError("A SQLite database file is required.")
        database_path = Path(self.config.database_path)
        if not database_path.parent.exists() or not database_path.parent.is_dir():
            raise DatabaseConfigurationError("The database directory does not exist.")
        if database_path.exists() and not database_path.is_file():
            raise DatabaseConfigurationError("The configured database path is not a file.")
        if require_existing_database and not database_path.is_file():
            raise DatabaseConfigurationError("The selected SQLite database file does not exist.")

    def connect(self, *, require_existing_database: bool = False) -> sqlite3.Connection:
        """Open a standard SQLite connection owned by the caller."""

        self.validate(require_existing_database=require_existing_database)
        return sqlite3.connect(self.config.database_path)

    def test_connection(self) -> None:
        """Open the selected database, execute SELECT 1, and close it."""

        connection: sqlite3.Connection | None = None
        try:
            connection = self.connect(require_existing_database=True)
            result = connection.execute("SELECT 1").fetchone()
            if result != (1,):
                raise DatabaseConfigurationError("The database returned an unexpected test result.")
        except sqlite3.Error as exc:
            raise DatabaseConfigurationError(f"SQLite connection failed: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    def create_database(self) -> Path:
        """Create a new empty SQLite file without overwriting existing data."""

        self.validate()
        database_path = Path(self.config.database_path)
        if database_path.exists():
            raise DatabaseConfigurationError("The selected database file already exists.")
        connection = self.connect()
        try:
            connection.execute("SELECT 1").fetchone()
        finally:
            connection.close()
        return database_path

    def create_orm_engine(self) -> Engine:
        """Build the existing SQLAlchemy engine through the central configuration."""

        self.validate()
        return create_engine(self.sqlalchemy_url())

    def sqlalchemy_url(self) -> URL:
        """Return the SQLAlchemy URL required by the existing ORM layer."""

        return URL.create("sqlite", database=self.config.database_path)
