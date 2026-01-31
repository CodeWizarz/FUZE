import logging
import sys
import structlog
from typing import Any, Dict

def add_temporal_context(logger: logging.Logger, method_name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Processor to extract Temporal context from stdlib logger record.
    Temporal SDK stores 'extra' fields like temporal_workflow_id.
    """
    # When using structlog.stdlib.LoggerFactory, the record is available via _record? 
    # Or strict mapping. 
    # Actually, if we use standard logging integration, we can pull from thread locals if Temporal sets them 
    # OR we rely on 'extra' being merged.
    
    # structlog.stdlib.ProcessorFormatter handles merging 'extra' from standard logging into event_dict.
    # So if Temporal does `logger.info("msg", extra={"temporal_workflow_id": "..."})`
    # it will appear in event_dict.
    
    # We can rename them to match user request.
    if "temporal_workflow_id" in event_dict:
        event_dict["workflow_id"] = event_dict.pop("temporal_workflow_id")
    if "temporal_run_id" in event_dict:
        event_dict["run_id"] = event_dict.pop("temporal_run_id")
    if "temporal_activity_id" in event_dict:
        event_dict["activity_id"] = event_dict.pop("temporal_activity_id")
    
    return event_dict

def configure_logging():
    """
    Configure structured logging for the application.
    Interprets stdlib logging calls and outputs JSON.
    """
    
    # Processors applied to all loggers
    processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.contextvars.merge_contextvars, # Support for thread-local/contextvar context
        add_temporal_context, # Custom remapping
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer()
    ]

    # Configure Structlog
    structlog.configure(
        processors=processors,
        # Use stdlib logger factory to support existing Python logging calls
        logger_factory=structlog.stdlib.LoggerFactory(),
        # Wrapper class that routes structlog calls to stdlib
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure Standard Library Logging
    # This intercepts all standard logging calls (including Temporal SDK's) and runs them through processors.
    
    handler = logging.StreamHandler(sys.stdout)
    # Use structlog to format the output of stdlib logs
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=processors,
    ))
    
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    
    # Silence overly verbose loggers if needed
    logging.getLogger("temporalio").setLevel(logging.INFO)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
