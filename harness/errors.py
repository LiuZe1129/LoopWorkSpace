"""Agent-Harness 稳定错误类型。

该模块位于 Harness 控制层与 GraphFlow/CLI 调用方之间，将状态冲突、持久化冲突、
租约冲突和数据缺失转换成可分类错误，避免上层依赖底层文件系统异常。
"""


class HarnessError(Exception):
    """Harness 基础异常，位于内部错误向 GraphFlow TaskResult 映射的边界。"""


class AlreadyExistsError(HarnessError):
    """资源重复错误，位于 Harness Task/Execution/Attempt 首次持久化检查环节。"""


class NotFoundError(HarnessError):
    """资源不存在错误，位于 Harness 根据稳定 ID 恢复执行状态的环节。"""


class InvalidTransitionError(HarnessError):
    """非法状态转换错误，位于 Harness 生命周期状态机的强制校验边界。"""


class VersionConflictError(HarnessError):
    """状态版本冲突错误，位于 Harness 持久化 compare-and-swap 并发控制环节。"""


class LeaseConflictError(HarnessError):
    """执行租约冲突错误，位于 Harness 防止多个 Worker 同时运行一个 Execution 的边界。"""


class LeaseOwnershipError(HarnessError):
    """租约所有权错误，位于 Harness Worker 续租或释放执行权的校验环节。"""


class ContextBudgetError(HarnessError):
    """上下文预算错误，位于 Harness Context Manager 组装模型输入的限制环节。"""


class AgentProfileIntegrityError(HarnessError):
    """AgentProfile 完整性错误，位于 Harness 加载版本化 Prompt 的哈希校验边界。"""


class LedgerConflictError(HarnessError):
    """幂等 Ledger 冲突错误，位于 Harness 阻止同一键对应不同 ToolCall 的边界。"""


class InfrastructureUnavailableError(HarnessError):
    """执行基础设施不可用错误，位于 Harness 禁止回退宿主 Shell 的安全边界。"""


class SandboxNotFoundError(HarnessError):
    """Sandbox 不存在错误，位于 Harness Tool Router 定位 Attempt 容器的执行环节。"""


class WorkspaceLimitError(HarnessError):
    """工作区限额错误，位于 Harness Sandbox 防止 Tool 失控写入磁盘的边界。"""


class WorkspaceConflictError(HarnessError):
    """本地源码并发冲突错误，位于 Harness 验收通过后自动回写宿主工程的安全边界。"""


class WorkspacePreparationError(HarnessError):
    """源码快照准备错误，位于 Harness Task 启动与 Sandbox Workspace 创建之间。"""


class InternalExecutionError(HarnessError):
    """未分类执行错误，位于 Harness 异常收尾到稳定 TaskResult 错误映射的边界。"""


class BudgetExceededError(HarnessError):
    """预算耗尽错误，位于 Harness 每轮模型或 Tool 调用前后的强制停止边界。"""

    def __init__(self, dimension: str, message: str) -> None:
        super().__init__(message)
        self.dimension = dimension


class CancellationRequestedError(HarnessError):
    """取消请求错误，位于 Harness Agent Loop 向模型、Tool 和 Sandbox 传播停止信号的边界。"""


class PauseRequestedError(HarnessError):
    """暂停请求错误，位于 Harness Agent Loop 到达安全 Checkpoint 后让出执行权的边界。"""
