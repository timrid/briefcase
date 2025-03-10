from __future__ import annotations

import dataclasses
import enum
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, TextIO

from briefcase.console import Console
from briefcase.exceptions import BriefcaseCommandError, BriefcaseConfigError


class DebuggerMode(enum.StrEnum):
    SERVER = "server"
    CLIENT = "client"


@dataclasses.dataclass
class DebuggerConfig:
    mode: str | None
    ip: str | None
    port: int | None


def remote_debugger_config_from_string(
    remote_debugger_config: str,
) -> tuple[str, DebuggerConfig]:
    """
    Convert a remote debugger config string into a DebuggerConfig object.

    The config string is expected to be in the form:
        "[DEBUGGER[,[IP:]PORT][,MODE]]"

    Config examples:
        ""
        "pdb"
        "pdb,5678"
        "pdb,,server"
        "pdb,localhost:5678,server"
    """
    debugger = ip_and_port = ip = port = mode = None
    parts = remote_debugger_config.split(",")
    if len(parts) == 1:
        debugger = parts[0]
    elif len(parts) == 2:
        debugger, ip_and_port = parts
    elif len(parts) == 3:
        debugger, ip_and_port, mode = parts
    else:
        raise BriefcaseCommandError(
            f"Invalid remote debugger specification: {remote_debugger_config}"
        )

    if ip_and_port is not None:
        parts = ip_and_port.split(":")
        if len(parts) == 1:
            port = parts[0]
            if port == "":
                port = None
        elif len(parts) == 2:
            ip = parts[0]
            port = parts[1]
        else:
            raise BriefcaseCommandError(
                f"Invalid remote debugger specification: {remote_debugger_config}"
            )

    if port is not None:
        try:
            port = int(port)
        except ValueError:
            raise BriefcaseCommandError(f"Invalid remote debugger port: {port}")

    return debugger, DebuggerConfig(mode=mode, ip=ip, port=port)


STARTUP_MODULE = "_briefcase"


class BaseDebugger(ABC):
    """Definition for a plugin that defines a new Briefcase debugger."""

    supported_modes: ClassVar[list[DebuggerMode]]
    default_mode: ClassVar[DebuggerMode]
    default_ip: ClassVar[str] = "localhost"
    default_port: ClassVar[int] = 5678

    def __init__(self, console: Console, config: DebuggerConfig) -> None:
        self.console = console
        self.mode: DebuggerMode = DebuggerMode(config.mode or self.default_mode)
        self.ip: str = config.ip or self.default_ip
        self.port: int = config.port or self.default_port

        if self.mode not in self.supported_modes:
            raise BriefcaseConfigError(
                f"Unsupported debugger mode: {self.mode} for {self.__class__.__name__}"
            )

    @property
    def additional_requirements(self) -> list[str]:
        """Return a list of additional requirements for the debugger."""
        return []

    @abstractmethod
    def create_startup_file(self, file: TextIO, path_mappings: str) -> None:
        """
        Create the code that is necessary to start the debugger.

        :param file: The file to write the startup code to.
        :param path_mappings: The path mappings that should be used in the startup file.
        """
        raise NotImplementedError()

    def write_startup_file(
        self,
        app_path: Path,
        pth_folder_path: Path | None,
        path_mappings: str,
    ):
        """
        Write the debugger startup file and create a .pth file to import it automatically at startup.

        :param app_path: The path to the application folder.
        :param pth_folder_path: The path to the folder where the .pth file should be created.
        :param path_mappings: The path mappings that should be used in the startup file.
        """
        startup_code_path = app_path / f"{STARTUP_MODULE}.py"
        with startup_code_path.open("w", encoding="utf-8") as f:
            self.create_startup_file(f, path_mappings)

        if pth_folder_path:
            startup_pth_path = pth_folder_path / f"{STARTUP_MODULE}.pth"
            with startup_pth_path.open("w", encoding="utf-8") as f:
                f.write(f"import {STARTUP_MODULE}")
