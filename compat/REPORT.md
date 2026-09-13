# Compatibility report

Upstream: unstructured 0.27.5 (Python 3.12.13, Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.43), import 0.87s.  
Candidate: tryworks 0.1.0 (Python 3.12.13, Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.43), import 0.057s.

Elements are aligned by normalized text; PageBreak elements are ignored. PDFs use the fast strategy.

**Overall:** 97% of upstream elements have an identical-text counterpart; of those, 97% have the same type and 94% the same element id.

| Document | Upstream elements | tryworks elements | Text similarity | Aligned | Same type | Same id | Upstream s | tryworks s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| arrivals.csv | 1 | 1 | 100.0% | 1 | 100% | 100% | 0.5019 | 0.0702 |
| fleet.pptx | 8 | 8 | 100.0% | 8 | 100% | 100% | 2.4748 | 0.1456 |
| harbor.html | 11 | 11 | 100.0% | 11 | 100% | 100% | 0.4172 | 0.0416 |
| inventory.xlsx | 4 | 4 | 100.0% | 4 | 100% | 100% | 0.6982 | 0.0626 |
| memo.docx | 14 | 14 | 100.0% | 14 | 100% | 100% | 0.0883 | 0.0344 |
| onboarding.md | 11 | 11 | 100.0% | 11 | 82% | 100% | 0.1126 | 0.0334 |
| policy.txt | 7 | 7 | 100.0% | 7 | 100% | 100% | 0.2181 | 0.0183 |
| report.pdf | 11 | 12 | 98.1% | 9 | 100% | 56% | 2.5977 | 0.1506 |
| schedule.eml | 5 | 5 | 100.0% | 5 | 100% | 100% | 0.1207 | 0.037 |

## Metadata agreement on aligned elements

| Field | Agree | Compared |
|---|---:|---:|
| `category_depth` | 94% | 32 |
| `emphasized_text_contents` | 100% | 4 |
| `filetype` | 100% | 70 |
| `header_footer_type` | 100% | 2 |
| `languages` | 99% | 70 |
| `link_urls` | 100% | 3 |
| `page_name` | 100% | 4 |
| `page_number` | 100% | 33 |
| `parent_id` | 100% | 41 |
| `sent_from` | 100% | 3 |
| `subject` | 100% | 3 |
| `text_as_html` | 86% | 7 |

## Differences

### arrivals.csv

```
field   languages: upstream=["nld"] tryworks=null on 'Vessel Port Arrival Tons Marlin Rotterdam 2026-08-01 42.5 Pe'
```

### memo.docx

```
field   text_as_html: upstream="<table><tr><td>Warehouse totals</td><td>Warehouse totals</td><td>Unit tryworks="<table><tr><td colspan=\"2\">Warehouse totals</td><td>Units</td></tr> on 'Warehouse totals Units Rotterdam KR-1001 1200 Antwerp KR-204'
```

### onboarding.md

```
type    NarrativeText -> ListItem: 'Finance reviews anything larger'
field   category_depth: upstream=null tryworks=1 on 'Finance reviews anything larger'
type    NarrativeText -> ListItem: 'Keep receipts for every order'
field   category_depth: upstream=null tryworks=1 on 'Keep receipts for every order'
```

### report.pdf

```
replace upstream [ListItem:'Rotterdam throughput up eleven percent • Two new procurement'] tryworks [ListItem:'Rotterdam throughput up eleven percent' | ListItem:'Two new procurement analysts hired']
insert  upstream [] tryworks [NarrativeText:'Southern margins improved after the team renegotiated fuel s']
delete  upstream [NarrativeText:'Southern margins improved after the team renegotiated fuel s'] tryworks []
```

