# ADR-0011: Passed boards of a batch extend the reference

- Status: accepted
- Date: 2026-10-07

## Context

With a single reference photo, every way in which good boards differ from it (fillet shape, lot marking, a part
placed 1–2 px differently) looks like a defect. The difference map copes by tolerating such variation, and with it
misses real defects of the same size: missing fillets, small part shifts, hairs. Operators need the service to work
from the first board of a batch, so a statistical model of variation learned from many boards is not an option.

## Decision

Targeted detectors compare a photo with a *pool*: the reference plus the first N (default 2) boards inspected
against the same reference that the operator passed (board verdict `pass`) and on which no region was confirmed
as a defect. A finding must differ from every pool member. A board joins the pool only after its own inspection;
the pool is chosen by the worker when an inspection runs (earliest qualifying boards first). Pool photos are the
stored aligned photos; per-photo measurements the detectors need later are stored as `measures.json` next to them.

## Consequences

- The first boards of a batch are checked against the reference only; from the third passed board on, variation
  seen on passed boards no longer produces findings.
- A defect that was on a pool board and not confirmed stays invisible on later boards. Operators must confirm
  real defects (accepted verdict), otherwise the board would enter the pool.
- Results of an inspection depend on earlier verdicts in the session; the pool used is identified by the stored
  inspections, so it can be reconstructed.
