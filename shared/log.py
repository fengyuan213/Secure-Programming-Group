#!/usr/bin/env python3
"""
SOCP v1.3 Logging Configuration

Centralized logging setup for consistent formatting across the project.
Supports both development (console) and production (file) modes.

Usage:
    from shared.log import get_logger
    
    logger = get_logger(__name__)
    logger.info("Starting server...")
    logger.error("Connection failed", extra={"user_id": "123", "host": "localhost"})
"""

from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import Optional, Dict, Any
import os


# ========================================
#           LOGGING FORMATTERS
# ========================================

class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output with SOCP context"""
    
    # ANSI Color codes
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green  
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'       # Reset
    }
    
    def format(self, record: logging.LogRecord) -> str:
        # Add color to level name
        if record.levelname in self.COLORS:
            record.levelname = f"{self.COLORS[record.levelname]}{record.levelname}{self.COLORS['RESET']}"

        return super().format(record)


class GenericFormatter(logging.Formatter):
    
    def format(self, record: logging.LogRecord) -> str:
        # Add SOCP context if available
        socp_context = []
        
        # Extract common SOCP fields from extra data
        if hasattr(record, 'server_id'):
            socp_context.append(f"server={record.server_id[:8]}...")
        if hasattr(record, 'user_id'):
            socp_context.append(f"user={record.user_id[:8]}...")
        if hasattr(record, 'msg_type'):
            socp_context.append(f"msg={record.msg_type}")
        if hasattr(record, 'connection_id'):
            socp_context.append(f"conn={record.connection_id}")
            
        # Add context to message if present
        if socp_context:
            context_str = f"[{' '.join(socp_context)}] "
            record.msg = f"{context_str}{record.msg}"
            
        return super().format(record)


# ========================================
#           LOGGING CONFIGURATION
# ========================================

_loggers_configured = set()

def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """
    Get a configured logger for the given module.
    
    Args:
        name: Usually __name__ from the calling module
        level: Override log level ("DEBUG", "INFO", "WARNING", "ERROR")
        
    Returns:
        Configured logger instance
        
    Examples:
        logger = get_logger(__name__)
        logger.info("Server starting")
        
        # With context
        logger.error("Connection failed", extra={
            "user_id": "user-123",
            "server_id": "server-456", 
            "msg_type": "USER_HELLO"
        })
    """
    logger = logging.getLogger(name)
    
    # Only configure each logger once
    if name not in _loggers_configured:
        _configure_logger(logger, level)
        _loggers_configured.add(name)
        
    return logger


def _configure_logger(logger: logging.Logger, level: Optional[str] = None) -> None:
    """Configure a logger with appropriate handlers and formatters"""
    
    # Determine log level
    log_level = _get_log_level(level)
    logger.setLevel(log_level)
    
    # Clear existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # Environment detection
    is_development = _is_development()
    print(f"support colour: {_supports_color()}"   )
    if is_development:
     
        _add_file_handler(logger)
        _add_console_handler(logger, colored=True)
    else:
        # Production: Clean console + file logging
        _add_console_handler(logger, colored=False)
        _add_file_handler(logger)
    
    # Prevent duplicate messages from parent loggers
    logger.propagate = False


def _get_log_level(level: Optional[str] = None) -> int:
    """Determine appropriate log level"""
    
    if level:
        return getattr(logging, level.upper(), logging.INFO)
    
    
    # Default based on environment
    return logging.DEBUG if _is_development() else logging.INFO


def _is_development() -> bool:
    """Detect if we're in development mode"""
    return (
        os.getenv('PYTHON_ENV', '').lower() in ['dev', 'development'] or
        'pytest' in sys.modules or
        __debug__  # Python -O flag not used
    )


def _add_console_handler(logger: logging.Logger, colored: bool = True) -> None:
    """Add console handler with appropriate formatter"""
    
    fmt='[%(levelname)-8s][%(asctime)s][%(name)-5s]: %(message)s'
    handler = logging.StreamHandler(sys.stdout)
    
    if colored and _supports_color():
        formatter = ColoredFormatter(
            fmt=fmt,
            datefmt='%H:%M:%S'
        )
    else:
        formatter = GenericFormatter(
            fmt=fmt,
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def _add_file_handler(logger: logging.Logger) -> None:
    """Add file handler for production logging"""
    
    # Create logs directory
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # File handler with rotation would be nice, but keeping simple for now
    log_file = log_dir / "socp.log"
    handler = logging.FileHandler(log_file)
    
    formatter = GenericFormatter(
        fmt='%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def _supports_color() -> bool:
    """Check if terminal supports color output"""

    # stdout must be a terminal
    if not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        return False

    # TERM should not be dumb
    if os.getenv("TERM", "") == "dumb":
        return False

    # Windows-specific check
    if sys.platform == "win32":
        # On modern Windows terminals, ANSI colors are supported
        return os.getenv("ANSICON") is not None or os.getenv("WT_SESSION") is not None or os.getenv("TERM_PROGRAM") == "vscode" or "WindowsTerminal" in os.getenv("TERM", "")
        # Or just return True unconditionally if you know your terminal supports it
        # return True

    return True
# ========================================
#           CONVENIENCE FUNCTIONS  
# ========================================

def configure_root_logging(level: str = "INFO") -> None:
    """
    Configure root logging for the entire application.
    Call this once at application startup.
    
    Args:
        level: Root log level ("DEBUG", "INFO", "WARNING", "ERROR")
    """
    root_logger = logging.getLogger()
    _configure_logger(root_logger, level)


def log_socp_message(logger: logging.Logger, level: str, message: str, 
                    envelope: Optional[Dict[str, Any]] = None, 
                    **context: Any) -> None:
    """
    Log a SOCP protocol message with structured context.
    
    Args:
        logger: Logger instance
        level: Log level ("debug", "info", "warning", "error")
        message: Log message
        envelope: SOCP envelope dict for automatic context extraction
        **context: Additional context fields
        
    Example:
        log_socp_message(logger, "info", "Processing message", 
                        envelope=envelope_dict, connection_type="user")
    """
    
    extra_context = {}
    
    # Extract context from envelope
    if envelope:
        extra_context.update({
            'msg_type': envelope.get('type'),
            'from_id': envelope.get('from'), 
            'to_id': envelope.get('to'),
            'timestamp': envelope.get('ts')
        })
    
    # Add additional context
    extra_context.update(context)
    
    # Log with context
    log_func = getattr(logger, level.lower())
    log_func(message, extra=extra_context)


