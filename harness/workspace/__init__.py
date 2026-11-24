"""Agent-Harness 本地工程 Workspace 基础设施。

该包位于 Task 输入与 Sandbox 创建/结果回写之间，负责本地源码快照、隔离副本、
ChangeSet、冲突检测和验收后自动回写，不要求用户预先创建 Git commit。
"""

from .contracts import ChangeSet, WorkspaceFile, WorkspaceSession
from .manager import WorkspaceManager

__all__ = ["ChangeSet", "WorkspaceFile", "WorkspaceManager", "WorkspaceSession"]
