# Local brand assets

This integration ships its Home Assistant brand images in:

```text
custom_components/smartthings_find/brand/
```

Included files:

- `icon.png` — 256×256 transparent PNG
- `icon@2x.png` — 512×512 transparent PNG
- `logo.png` and `logo@2x.png` — light-theme SmartThings wordmark
- `dark_logo.png` and `dark_logo@2x.png` — dark-theme SmartThings wordmark

Home Assistant 2026.3 and later serves these files through the local Brands Proxy API. The integration-local assets take precedence over legacy CDN assets.

The v1.3.4 refresh reapplies the final SmartThings icon directly inside the integration release package. No external Brands repository is required at runtime.
