# What the type check knows of APScheduler 3 (#228). It ships no type information and no stub
# package describes it, so this does — for the part the bot uses and no more: the scheduler in
# `src/services/scheduler_service.py`, its jobs, its three triggers and its job store. A use
# this does not describe is a type error, not a silent `Any`: extend the stub, don't silence
# the call. `tests/unit/test_library_stubs.py` holds it to the installed APScheduler.
