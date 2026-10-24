# Wallex API read-only test

This is a manual GitHub Actions smoke test. It does not make trades or call order endpoints, and it does not change Tbot.

## Add to a repository

Copy these files into the root of the target GitHub repository, preserving the paths:

- `.github/workflows/wallex-api-test.yml`
- `scripts/wallex_test.py`

## Add the API key as a GitHub Actions secret

In the repository, open **Settings → Secrets and variables → Actions → New repository secret**.

- Name: `WALLEX_API_KEY`
- Secret: the Wallex API key

Do not put the key into either file or any commit.

## Run

Open **Actions → Wallex API read-only test → Run workflow**. It tests the public `/v1/markets` endpoint and, when the secret is present, the authenticated `/v1/otc/markets` endpoint. The log lists detected USDT/TMN, TON and GRAM markets and calculates an estimate from USDT pairs when a direct Toman pair is not found.
