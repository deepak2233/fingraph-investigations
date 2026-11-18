# Dataset attribution

The model experiment uses IBM AMLworld HI-Small, distributed by Erik Altman and IBM through the linked Kaggle dataset. The paper is *Realistic Synthetic Financial Transactions for Anti-Money Laundering Models* by Altman and colleagues, NeurIPS 2023.

- [Official repository and license statement](https://github.com/IBM/AML-Data)
- [Official dataset distribution](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml)
- [CDLA-Sharing-1.0 license](https://cdla.dev/sharing-1-0/)
- [Research paper](https://arxiv.org/abs/2306.16424)

The source data is licensed under CDLA-Sharing-1.0. The small derivative excerpt in `artifacts/inference_examples.json` is distributed under those same data terms with this attribution. Changes include row selection, bank-scoped account identifiers, ISO timestamps, graph features, and reference model outputs. The complete source CSV is excluded from the repository.

`data/cases.json` and `data/ledger_snapshot.json` are authored synthetic examples created for FinGraph. They are separate from the IBM dataset.
