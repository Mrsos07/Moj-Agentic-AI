"""
AEOS — AI Employee Operating System
====================================

An operating system for autonomous AI Employees.

Core principle: One Connected Account = One AI Employee.

The runtime is temporary. The employee is permanent.

An event (scheduler / webhook / manual / mention / reply / notification / time
trigger) causes the RuntimeManager to load an employee, build an execution
context, run exactly one cognitive decision cycle, persist everything, then
destroy the runtime.

This package is additive: it does not replace the existing chatbot subsystem.
"""

__version__ = "1.0.0"
