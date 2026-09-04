# Local brand assets

This integration ships its Home Assistant brand images in:

```text
custom_components/smartthings_find/brand/
```

Included files:

- `icon.png` — 256×256 transparent PNG containing only the SmartThings Find locator mark
- `icon@2x.png` — 512×512 transparent PNG containing only the locator mark
- `logo.png` and `logo@2x.png` — light-theme horizontal logo with the locator mark before the `SmartThings Find` wordmark
- `dark_logo.png` and `dark_logo@2x.png` — dark-theme horizontal logo with the locator mark before a white wordmark

Home Assistant 2026.3 and later serves these files through the local Brands Proxy API. The integration-local assets take precedence over legacy CDN assets.

The v1.3.5 refresh uses the supplied SmartThings Find artwork for both the standalone icon and the horizontal logos. No external Brands repository is required at runtime.
