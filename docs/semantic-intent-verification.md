# Semantic Intent Verification 2.0

## Problem
Current Intent Verification relies on Jaccard keyword overlap and produces false drift signals for semantically aligned wording.

## Goal
Add local semantic similarity and explicit constraint checks while preserving the lexical method as a measurable baseline.

## Safety Rules
- ATTVE remains authoritative for transaction safety.
- Transaction intent evidence must not inflate operational risk in advisory mode.
- Semantic model failure must use a documented lexical fallback.
- No paid API or secret key is required.
- The existing ALLOW, WARN, and BLOCK pipeline remains compatible.
- Intent Verification will not independently BLOCK in the first version.

## Initial Scope
- English intent comparison
- Cursor, n8n, and transaction events
- Lexical baseline plus semantic similarity
- Amount, quantity, target, and destructive-action constraints
- Unit tests, regression tests, and baseline comparison
- Clear explanations and honest limitations
