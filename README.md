<<<<<<< HEAD
# HA-SmartThings-Find
Home Assisstant custom integration to provide data from Samsung SmartThings Find, such as SmartTag locations
=======
# HA-SmartThings-Find (Cookie Auth)

A HACS-ready fork of **Vedeneb/HA-SmartThings-Find** adapted for Samsung's current login flow.

- QR login endpoints are no longer available.
- Authentication is done by pasting your browser Cookie header for `chkLogin.do`.

## Install (HACS)
1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/1bobby-git/HA-SmartThings-Find` as **Integration**
3. Install and restart Home Assistant

## Setup
1. Log in at `https://smartthingsfind.samsung.com/` in your browser.
2. Open DevTools → **Network**
3. Find the request `chkLogin.do` (refresh the page if needed)
4. Copy **Request Headers → Cookie:** line (the whole line)
5. In Home Assistant, add the **SmartThings Find** integration and paste the cookie line.

## Notes
- Cookies expire; use reauth when needed.
- This integration uses reverse engineered endpoints and may break if Samsung changes the website.
>>>>>>> 0c3ec26 (Initial HACS-readyt release (cookie auth))
