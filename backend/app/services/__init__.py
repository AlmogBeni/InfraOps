"""Infrastructure integration service layers.

Services encapsulate all external-system communication (vCenter, Windows
guests, certificate stores, software repositories). HTTP route handlers never
talk to infrastructure APIs directly.
"""
