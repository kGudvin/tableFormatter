from app.domain.models import ProcessingState

_TRANSITIONS: dict[ProcessingState, set[ProcessingState]] = {
    ProcessingState.NEW: {
        ProcessingState.WAITING_FOR_FINAL_PROTOCOL,
        ProcessingState.FINAL_PROTOCOL_FOUND,
        ProcessingState.CANCELLED,
        ProcessingState.REVIEW_REQUIRED,
        ProcessingState.FAILED,
    },
    ProcessingState.WAITING_FOR_FINAL_PROTOCOL: {
        ProcessingState.FINAL_PROTOCOL_FOUND,
        ProcessingState.CANCELLED,
        ProcessingState.REVIEW_REQUIRED,
        ProcessingState.FAILED,
    },
    ProcessingState.FINAL_PROTOCOL_FOUND: {
        ProcessingState.WAITING_FOR_CONTRACT,
        ProcessingState.CONTRACT_FOUND,
        ProcessingState.REVIEW_REQUIRED,
        ProcessingState.FAILED,
    },
    ProcessingState.WAITING_FOR_CONTRACT: {
        ProcessingState.CONTRACT_FOUND,
        ProcessingState.REVIEW_REQUIRED,
        ProcessingState.FAILED,
    },
    ProcessingState.CONTRACT_FOUND: {
        ProcessingState.WAITING_FOR_SPECIFICATION,
        ProcessingState.SPECIFICATION_FOUND,
        ProcessingState.REVIEW_REQUIRED,
        ProcessingState.FAILED,
    },
    ProcessingState.WAITING_FOR_SPECIFICATION: {
        ProcessingState.SPECIFICATION_FOUND,
        ProcessingState.REVIEW_REQUIRED,
        ProcessingState.FAILED,
    },
    ProcessingState.SPECIFICATION_FOUND: {
        ProcessingState.MONITORING_AMENDMENTS,
        ProcessingState.COMPLETED,
        ProcessingState.REVIEW_REQUIRED,
        ProcessingState.FAILED,
    },
    ProcessingState.MONITORING_AMENDMENTS: {
        ProcessingState.MONITORING_AMENDMENTS,
        ProcessingState.COMPLETED,
        ProcessingState.REVIEW_REQUIRED,
        ProcessingState.FAILED,
    },
    ProcessingState.COMPLETED: {ProcessingState.MONITORING_AMENDMENTS, ProcessingState.REVIEW_REQUIRED},
    ProcessingState.CANCELLED: {ProcessingState.MONITORING_AMENDMENTS, ProcessingState.REVIEW_REQUIRED},
    ProcessingState.FAILED: {
        ProcessingState.WAITING_FOR_FINAL_PROTOCOL,
        ProcessingState.REVIEW_REQUIRED,
        ProcessingState.FAILED,
    },
    ProcessingState.REVIEW_REQUIRED: {
        ProcessingState.REVIEW_REQUIRED,
        ProcessingState.WAITING_FOR_FINAL_PROTOCOL,
        ProcessingState.COMPLETED,
    },
}


def can_transition(current: ProcessingState, target: ProcessingState) -> bool:
    return current == target or target in _TRANSITIONS[current]


def transition(current: ProcessingState, target: ProcessingState) -> ProcessingState:
    if can_transition(current, target):
        return target
    msg = f"Illegal state transition: {current} -> {target}"
    raise ValueError(msg)

