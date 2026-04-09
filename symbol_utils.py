import requests

from constants import ALL_SYMBOLS, HL_INFO_URL

DEFAULT_MIN_VOLUME_24H = 10_000_000


def discover_usdc_perps(min_volume_24h: float = DEFAULT_MIN_VOLUME_24H) -> list[str]:
    try:
        resp = requests.post(
            HL_INFO_URL, json={"type": "metaAndAssetCtxs"}, timeout=15
        )
        resp.raise_for_status()
        meta, asset_ctxs = resp.json()

        perps = []
        for asset, ctx in zip(meta.get("universe", []), asset_ctxs):
            if asset.get("isDelisted", False):
                continue
            if asset.get("onlyIsolated"):
                continue
            margin_mode = asset.get("marginMode")
            if margin_mode in ("strictIsolated", "noCross"):
                continue
            vol = float(ctx.get("dayNtlVlm", 0))
            if vol < min_volume_24h:
                continue
            perps.append(asset["name"])
        if perps:
            return sorted(perps)
        return list(ALL_SYMBOLS)
    except Exception:
        return list(ALL_SYMBOLS)
