# 公的資料と確認済み統計

最終確認日: 2026-08-29

この文書には公的資料に記載された値と、モデル設計上の関連研究をまとめます。`examples/mvp.toml`は完全な合成設定です。`configs/inzai_chiba_new_town.toml`は、この文書に根拠を示す観測基準と、[assumptions.md](assumptions.md)に示す仮定値を組み合わせています。

## 日本の再生水利用（令和4年度）

国土交通省「下水処理水の再利用水質基準等マニュアル（案）」の総説には、令和4年度について次が記載されています。

| 項目 | 公的資料の値 |
|---|---:|
| 全国の下水処理場 | 2,133か所 |
| 下水処理水放流量 | 年間約145億m³ |
| 再生水を場外送水・利用する処理場 | 475か所 |
| 場外で利用された再生水 | 年間約2.4億m³ |
| 放流量に対する割合 | 2%未満 |

出典:

- [国土交通省 再生水水質基準等マニュアル](https://www.mlit.go.jp/mizukokudo/sewerage/content/002002820.pdf) — PDF 6ページ（本文上の第1章1ページ）
- [国土交通省 再生水利用状況](https://www.mlit.go.jp/mizukokudo/sewerage/content/001989277.pdf) — 令和4年度の約2.4億m³／年、放流量の2%弱、用途別内訳
- [国土交通省 流域総合水管理](https://www.mlit.go.jp/mizukokudo/sewerage/content/002001196.pdf) — 流域単位で治水・利水・環境を一体的に扱う政策背景

上記資料では「事業場への直接供給」などの用途区分はありますが、国内データセンター専用の再生水利用量を全国集計した値は確認できませんでした。この記述は「利用がない」という意味ではなく、列挙資料の確認範囲で全国集計を特定できなかったという調査上の留保です。

## 水利用とデータセンター

- [DOE: Cooling Water Efficiency Opportunities for Federal Data Centers](https://www.energy.gov/cmei/femp/cooling-water-efficiency-opportunities-federal-data-centers) — WUEを施設水使用量[L]／IT機器電力量[kWh]として説明。冷却塔では蒸発とブローダウンが主要な水用途であることも説明。
- [USGS: Water-Use Terminology](https://www.usgs.gov/mission-areas/water-resources/science/water-use-terminology) — `consumptive use`を、取水のうち蒸発などにより即時利用できない部分として定義。取水と消費を分ける根拠として使用。
- [EPA: 2017 Potable Reuse Compendium](https://www.epa.gov/sites/production/files/2018-01/documents/potablereusecompendium_3.pdf) — 飲用再利用の科学、技術、政策、処理、リスク管理を整理。本MVPは水質安全性をモデル化していないため、再生水比率を技術的実現可能性の主張として扱わない。
- [LBNL: 2024 United States Data Center Energy Usage Report](https://eta-publications.lbl.gov/publications/2024-lbnl-data-center-energy-usage-report) — 米国データセンター電力利用の歴史推計と2028年までのシナリオ範囲。地域の日本向け入力値として直接転用していない。

## 印西・千葉県営水道の公表値

次の値は公的資料で確認した値です。ただし、公表値をそのまま印西専用の水量として扱うことと、モデルの入力へ採用することは別です。

| 項目 | 公表値 | 基準日・年度 | モデル上の扱い |
|---|---:|---|---|
| 北総浄水場の配水池有効容量 | 48,000 m³（48 ML） | 令和6年度 | 地域配水バッファの**容量代理値**。印西専用ではない |
| 千葉県営水道の一人一日平均給水量 | 280 L/人/日 | 令和6年度 | 合成住民需要の一人当たり参照値 |
| 千葉県営水道の一日平均給水量 | 864,986 m³/日 | 令和6年度 | 県営水道全体の背景統計。印西向け日量には使わない |
| 千葉県営水道の一日最大給水量 | 991,586 m³/日 | 令和6年度 | 県営水道全体の背景統計。印西向け日量には使わない |
| 印西市NT（印西）地区人口 | 61,058人 | 2026年7月末 | 対象人口近似の一部 |
| 印西市NT（印旛）地区人口 | 4,941人 | 2026年7月末 | 対象人口近似の一部 |
| 印西市NT（本埜）地区人口 | 4,761人 | 2026年7月末 | 対象人口近似の一部 |

出典:

- [千葉県営水道 令和6年度事業年報「20 施設概要」](https://www.pref.chiba.lg.jp/suidou/souki/toukeidata/documents/r6-20.pdf) — 北総浄水場の配水池は有効容量16,000 m³×3池、合計48,000 m³。これは浄水場施設の容量であり、印西市専用の貯留量や2026年6月1日の実貯水量ではない。
- [千葉県営水道「北総浄水場」](https://www.pref.chiba.lg.jp/suidou/jousui/shisetsu/hokusou.html) — 施設能力126,700 m³/日、水源は利根川。千葉ニュータウンだけでなく、成田ニュータウンや成田国際空港などにも給水する。
- [千葉県営水道「主要統計資料一覧（令和4年度から令和6年度）」](https://www.pref.chiba.lg.jp/suidou/souki/zigyougaiyou/toukei-ichiran-r4-r8.html) — 給水人口、給水量、一人一日平均給水量、施設利用率。
- [印西市「人口表（令和8年、令和7年分）」](https://www.city.inzai.lg.jp/0000017026.html) — 2026年7月末の地区別人口。3つのNT地区の合計70,760人を対象人口の近似に使用。
- [千葉県営水道の給水区域](https://www.pref.chiba.lg.jp/suidou/souki/suidoukyoku/kyuusuikuiki.html) — 印西市では千葉ニュータウンの列挙区域が県営水道区域。ただし人口統計のNT地区区分と給水区域境界は完全には一致しない。

公表された48 MLをモデルの容量境界へ採用する一方、全量を印西のための地域バッファとみなすこと、2026年6月1日に満水で開始することはシミュレーション仮定です。実際の同日貯留量、印西向け配分、運用余裕を示す公表値は確認できていません。

## 2025年の観測コンテキスト

2026年9月10日は成果物作成時点の2026年8月29日より後であり、2026年6月1日〜9月10日の完結した実績系列は存在しません。そのため、画面とログには直近の完結期間である2025年6月1日〜9月10日の観測値を、2026年の仮想シナリオと同じ月日へ対応させて表示します。年を置き換えた実測、2026年の予報、平年値のいずれでもありません。

| 観測系列 | 期間・粒度 | 確認値 | 水収支への扱い |
|---|---|---:|---|
| 気象庁 我孫子アメダス日降水量 | 2025-06-01〜09-10、日別 | 期間合計394.5 mm | 表示・ログだけ。流入へ変換しない |
| 利根川上流9ダム | 2025-06-01〜09-11、10日ごとの確定値 | 6/1: 498,240千m³、9/11: 156,520千m³ | 表示・診断だけ。印西向け配分に使わない |

出典と保存データ:

- [気象庁 我孫子 2025年6月の日別値](https://www.data.jma.go.jp/stats/etrn/view/daily_a1.php?block_no=0376&month=06&prec_no=45&year=2025)、[7月](https://www.data.jma.go.jp/stats/etrn/view/daily_a1.php?block_no=0376&month=07&prec_no=45&year=2025)、[8月](https://www.data.jma.go.jp/stats/etrn/view/daily_a1.php?block_no=0376&month=08&prec_no=45&year=2025)、[9月](https://www.data.jma.go.jp/stats/etrn/view/daily_a1.php?block_no=0376&month=09&prec_no=45&year=2025) — 保存した日別値102件の合計が394.5 mm。保存先は[`data/observed/jma_abiko_daily_precipitation_2025-06-01_2025-09-10.csv`](../data/observed/jma_abiko_daily_precipitation_2025-06-01_2025-09-10.csv)。我孫子は印西周辺の気象参照点で、上流ダム流域の降水を代表しない。
- [水資源機構「水源情報 過去のデータ」](https://www.water.go.jp/honsya/honsya/suigen/junpo/past/index.html) — 2025年の1日・11日・21日を中心とする確定値を転記。[6月1日の原表](https://www.water.go.jp/honsya/honsya/suigen/junpo/past/2025/junpo20250601.html)、[9月11日の原表](https://www.water.go.jp/honsya/honsya/suigen/junpo/past/2025/junpo20250911.html)。保存先は[`data/observed/tone_upper_9_reservoirs_2025_10day.csv`](../data/observed/tone_upper_9_reservoirs_2025_10day.csv)。7月には洪水期の制限容量へ分母が変わるため、容量・貯水率の単純な連続比較には注意が必要。
- [国土交通省 関東地方整備局「利根川水系」](https://www.ktr.mlit.go.jp/river/shihon/river_shihon00000111.html) — 利根川上流9ダムの構成と現況を確認する補助資料。

我孫子降水を上水流入へ換算するために必要な集水面積、流出率、取水可能率、浄水歩留まり、時間遅れは取得していません。9ダム値の観測点間は画面表示のため線形補間しますが、補間値は観測値ではありません。両系列とも`source_inflow_l`、住民供給、DC供給、配分政策の計算結果を変えません。

## 北総浄水場の水源系統と限界

- [千葉県営水道「水源」](https://www.pref.chiba.lg.jp/suidou/keikaku/suigen/suigen.html) — 木下取水場から北総浄水場へ送水し、北総系の水源施設として利根川河口堰、川治ダム、湯西川ダム、奈良俣ダムを掲載。
- [千葉県営水道 令和6年度事業年報「9 水源及び取水」](https://www.pref.chiba.lg.jp/suidou/souki/toukeidata/documents/r6-09.pdf) — 北総系の水源・水利権と月別取水量を確認できるが、印西向けの日別配分ではない。
- [国土交通省 関東地方整備局「鬼怒川上流ダム群」](https://www.ktr.mlit.go.jp/kinudamu/kinudamu00013.html) — 川治ダムと湯西川ダムを含む鬼怒川4ダムの広域供給関係を説明。

利根川上流9ダム系列に含まれる北総系水源は奈良俣ダムであり、川治ダムと湯西川ダムは鬼怒川4ダム側、利根川河口堰は河口部の流量・水位施設です。したがって、9ダム合計は北総浄水場の水源ポートフォリオ全体でも、北総・千葉ニュータウン・印西へ割り当てられる在庫でもありません。本プロジェクトでは広域の渇水状況を理解する観測コンテキストに限定します。

## 利用上の注意

印西プロファイルで計算へ使う公表参照は、対象人口の近似、一人一日平均給水量、北総浄水場配水池の公表容量です。ただし、48 MLを印西の地域配水バッファ代理値として満水開始させること、合成した日別供給枠を与えることは仮定です。DCのIT負荷・WUE・上水契約量、将来渇水軌跡も公開確認できていないため仮定値です。現実の予測には、北総系の実運用、印西向け配分、実施設水使用などの地域別データが必要です。

## LLMエージェント・シミュレーションの関連研究

- [Ryuki Hyodo, “Minimal Local Simulation Foundations for LLM- and VLM-Driven Agents in 2D and 3D Environments,” arXiv:2608.22833](https://arxiv.org/abs/2608.22833) — ローカルモデル、数値状態、限定履歴、監査可能なログ、仮説生成用途という設計上の関連研究として参照。

本プロジェクトは同論文やSD-AgentFoundryのコード、README、設定形式、プロンプト、画像、名称を使用・コピーしていません。水収支エンジン、エージェント境界、設定スキーマ、ログ形式、ゲーム画面を独立に実装しています。
