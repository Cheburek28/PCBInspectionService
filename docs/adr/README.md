# Architecture decision records

| # | Decision | Status |
|---|---|---|
| 0001 | [Inspection as a separate, client-agnostic service](0001-separate-service.md) | accepted |
| 0002 | [Asynchronous processing with 202 + long-polling](0002-async-processing-long-poll.md) | accepted |
| 0003 | [Celery with Redis for jobs](0003-celery-redis.md) | accepted |
| 0004 | [Clients may downscale photos to the working width](0004-client-downscales.md) | accepted |
| 0005 | [Coordinates in uploaded pixels after EXIF orientation, both frames](0005-coordinates.md) | accepted |
| 0006 | [The service advises, a human decides](0006-decision-support.md) | accepted |
| 0007 | [Blob storage behind an interface](0007-blob-storage-interface.md) | accepted |
| 0008 | [References belong to a session](0008-session-scoped-references.md) | accepted |
| 0009 | [Pluggable engines, versioned results](0009-engine-interface-and-versioning.md) | accepted |
| 0010 | [Synchronous SQLAlchemy in API and worker](0010-sync-sqlalchemy.md) | accepted |

New decision: copy the latest file, increment the number, describe context, decision, consequences.
