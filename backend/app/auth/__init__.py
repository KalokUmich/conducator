"""Authentication module (AWS SSO + Google OAuth).

Provides SSO login via device authorization flows:
- AWS IAM Identity Center (OIDC device flow)
- Google OAuth 2.0 (device flow)

Services:
    - SSOService: AWS IAM Identity Center device authorization.
    - GoogleSSOService: Google OAuth 2.0 device authorization.
"""
