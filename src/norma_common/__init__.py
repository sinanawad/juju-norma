"""Reusable, ops-free building blocks shared across the norma charms.

This package mirrors the shape of an eventual shared library (SYNTHESIS §D1):
class-A logic ported from the k8s sibling (event ledger, defer gate, config
validation, relation/secret helpers, bad-behavior test-bed, introspect
collectors) lands here as it is brought over phase by phase. It stays free of
any ``ops`` dependency so it remains plain-pytest testable and substrate-neutral.

P0 establishes the package; modules are added in later phases (P2+).
"""
