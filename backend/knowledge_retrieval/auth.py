"""
Role check for requests forwarded through api_gateway's proxy.

Same pattern as ai_inference/auth.py: the gateway injects X-User-Role after
verifying the caller's JWT and already gates the same paths itself
(routers/proxy.py _ROLE_ACL) — this is the defense-in-depth check for
callers that reach this service directly (its port is exposed to the host
in docker-compose for local dev, bypassing the gateway).
"""
from fastapi import Header, HTTPException, status


def require_role(*roles: str):
    allowed = set(roles)

    def _dependency(x_user_role: str = Header(default="")) -> set[str]:
        caller_roles = {r for r in x_user_role.split(",") if r}
        if not caller_roles & allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {sorted(allowed)}.",
            )
        return caller_roles

    return _dependency
