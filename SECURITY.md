# Security Policy

## Supported versions

| Version | Security updates |
| --- | --- |
| 1.4.x | Supported |
| 1.3.x and older | Not supported |

Install the latest release before reporting a security issue.

## Credentials and sensitive data

Never include any of the following in an issue, discussion, log excerpt, screenshot, or chat message:

- SmartThings Find `Cookie:` headers
- `JSESSIONID`, `WMONID`, CSRF values, or other session cookies
- cookies copied from `account.samsung.com`
- files under `.storage/smartthings_find_auth`
- Home Assistant backups containing those files

A cookie copied from `account.samsung.com` is not a supported SmartThings Find credential and may expose a broader Samsung Account session. This integration accepts only a Cookie header validated against `smartthingsfind.samsung.com`.

If a credential has been exposed, sign out of the Samsung Account on all devices, clear Samsung Account and SmartThings Find browser data, then create a new SmartThings Find session before updating the integration.

## Reporting a vulnerability

Use GitHub's private **Report a vulnerability** option when it is available for this repository. Otherwise, open a public issue containing only sanitized reproduction steps and request a private contact channel.

Include:

- affected integration version
- Home Assistant version
- minimal sanitized reproduction steps
- expected and actual behavior
- whether the issue affects confidentiality, integrity, or availability

Do not send live credentials as proof. Reports that require a real session can use redacted names and synthetic values.
