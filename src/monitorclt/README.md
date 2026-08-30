# monitorclt

A small client for the [U.S. Bureau of Economic Analysis (BEA) data API](https://apps.bea.gov/api/bea_web_service_api_user_guide.htm).

## Setup

The BEA API key (the "UserID" from https://apps.bea.gov/api/signup/) is read from the
`BEA_API_KEY` environment variable. Never commit the key to the repository.

```sh
export BEA_API_KEY=<your-key>
```

## Usage

From the command line (run from the `src` directory or with `src` on `PYTHONPATH`):

```sh
python -m monitorclt datasets                                  # list available datasets
python -m monitorclt parameters NIPA                           # list parameters of a dataset
python -m monitorclt values NIPA TableName                     # list valid values of a parameter
python -m monitorclt data NIPA TableName=T10101 Frequency=Q Year=2025   # fetch data
```

All commands print JSON to stdout. Pass `--api-key <key>` to override the environment variable.

From Python:

```python
from monitorclt import BeaApiClient

client = BeaApiClient()  # reads BEA_API_KEY from the environment
gdp = client.get_data("NIPA", TableName="T10101", Frequency="Q", Year="2025")
```
