# Security Policy

## Supported Versions

media-api ships as a single rolling release, versioned from git tags
(see the Versioning & Releases section of the project's `CLAUDE.md`). There
are no maintained LTS or backport branches — only the latest tagged release
is supported. If you're running an older version, please upgrade before
reporting an issue that may already be fixed.

This policy covers `media-api` only. The companion deployment repository
(`media-api-deploy`) is private and out of scope here.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

 Use GitHub's private vulnerability reporting: go to the **Security** tab of
 this repository and select **Report a vulnerability**. This opens a private
 advisory visible only to maintainers, so the issue can be discussed and
 fixed before any public disclosure.
 
 When reporting, please include:
 - A description of the vulnerability and its potential impact
 - Steps to reproduce, or a proof-of-concept if you have one
 - The affected version/commit

## What to Expect

- We aim to acknowledge new reports within **5 business days**.
- We'll work with you to understand and confirm the issue, and to agree on
  a disclosure timeline. As a rule of thumb we target a fix within **90
  days** for critical issues, sooner where practical, longer for lower
  severity — but this is a single-maintainer project, so timelines are
  best-effort rather than a contractual SLA.
- We'll credit reporters in the advisory and release notes, unless you'd
  prefer to stay anonymous.

## Scope Notes

- Dependency vulnerabilities are tracked via Dependabot alerts and are
  generally not something you need to report separately — check the
  Security tab first in case it's already known.
- This project uses a secret-scanning CI gate (`gitleaks`) on every PR.
  If you find a secret that made it into history despite that, please
  still report it privately rather than filing a public issue, since
  remediation may involve rotating credentials before disclosure.
