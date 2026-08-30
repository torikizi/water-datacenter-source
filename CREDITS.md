# Credits, References, and Provenance

Water Negotiation Labは新規に設計・実装したプロジェクトです。この文書は、設計上の参考資料、公的データ、外部接続先、同梱物の来歴を区別して示します。記載した資料のコードや画像が本プロジェクトへ含まれていることを意味しません。

## 独立実装

本プロジェクトのPythonコード、TOML設定形式、エージェント用プロンプト、JSONLスキーマ、README、ゲーム画面、SVG出力、名称は独立に作成しました。

SD-AgentFoundry、SD-AgentFoundry-2D、兵頭竜樹氏の関連リポジトリから、コード、README、設定形式、プロンプト、画像、名称を使用・コピーしていません。

`submission/assets/water-datacenter-concept.png`はOpenAIの画像生成（gpt-image 2.0）で本プロジェクト専用に生成した概念図です。関連研究や既存リポジトリから取得した画像ではなく、実在施設や観測結果を示す証拠画像としては使用していません。

## 設計上の関連研究

- Ryuki Hyodo, “Minimal Local Simulation Foundations for LLM- and VLM-Driven Agents in 2D and 3D Environments,” arXiv:2608.22833  
  https://arxiv.org/abs/2608.22833

ローカルモデル、数値状態、限定履歴、監査可能ログ、仮説生成用途という設計上の論点を確認するために参照しました。本プロジェクトの水収支エンジンやエージェント実装を同論文付随コードから移植したものではありません。

## 外部ランタイム

- DS4（DwarfStar）  
  https://github.com/antirez/ds4

DS4はOpenAI互換APIの外部ローカルサーバーとして接続します。DS4本体、モデル、モデル重み、キャッシュ、DS4由来コードは本リポジトリへ同梱しません。利用時はDS4側のライセンスとモデル側の利用条件を別途確認してください。

## デモ動画の合成音声

- VOICEVOX 0.25.2  
  https://voicevox.hiroshiba.jp/
- 音声ライブラリ: 玄野武宏（ノーマル）
- 必須クレジット: `VOICEVOX:玄野武宏(CV:ガロ)`
- VOICEVOXソフトウェア利用規約  
  https://voicevox.hiroshiba.jp/term/
- VirVox Project「VOICEVOXの利用規約」  
  https://www.virvoxproject.com/voicevox%E3%81%AE%E5%88%A9%E7%94%A8%E8%A6%8F%E7%B4%84

提出用デモ動画のナレーションは、インストール済みVOICEVOXをローカル実行して生成しました。キャラクター画像やVOICEVOX本体、音声モデルは同梱していません。動画終盤に必須クレジットを表示し、YouTube概要欄にも同じ表記を記載します。生成条件と区間は`submission/voicevox-narration.json`へ記録しています。生成音声を機械学習や音声モデル作成には使用しません。

## 公的データ・技術資料

モデルの用語、公開統計、印西プロファイルの観測基準に次を参照しました。

- 国土交通省「下水処理水の再利用水質基準等マニュアル」  
  https://www.mlit.go.jp/mizukokudo/sewerage/content/002002820.pdf
- 国土交通省「再生水利用状況」  
  https://www.mlit.go.jp/mizukokudo/sewerage/content/001989277.pdf
- 国土交通省「流域総合水管理」  
  https://www.mlit.go.jp/mizukokudo/sewerage/content/002001196.pdf
- 国土交通省 関東地方整備局「利根川水系」  
  https://www.ktr.mlit.go.jp/river/shihon/river_shihon00000111.html
- 千葉県営水道「主要統計資料一覧」  
  https://www.pref.chiba.lg.jp/suidou/souki/zigyougaiyou/toukei-ichiran-r4-r8.html
- 千葉県営水道「北総浄水場」  
  https://www.pref.chiba.lg.jp/suidou/jousui/shisetsu/hokusou.html
- 千葉県営水道 令和6年度事業年報「20 施設概要」  
  https://www.pref.chiba.lg.jp/suidou/souki/toukeidata/documents/r6-20.pdf
- 千葉県営水道「水源」  
  https://www.pref.chiba.lg.jp/suidou/keikaku/suigen/suigen.html
- 千葉県営水道「給水区域」  
  https://www.pref.chiba.lg.jp/suidou/souki/suidoukyoku/kyuusuikuiki.html
- 印西市「人口表」  
  https://www.city.inzai.lg.jp/0000017026.html
- 気象庁「我孫子 日別値（2025年6月〜9月）」  
  https://www.data.jma.go.jp/stats/etrn/view/daily_a1.php?block_no=0376&month=06&prec_no=45&year=2025
- 独立行政法人水資源機構「水源情報・全水系の確定値」  
  https://www.water.go.jp/honsya/honsya/suigen/junpo/past/index.html
- U.S. Department of Energy, “Cooling Water Efficiency Opportunities for Federal Data Centers”  
  https://www.energy.gov/cmei/femp/cooling-water-efficiency-opportunities-federal-data-centers
- U.S. Geological Survey, “Water-Use Terminology”  
  https://www.usgs.gov/mission-areas/water-resources/science/water-use-terminology
- U.S. Environmental Protection Agency, “2017 Potable Reuse Compendium”  
  https://www.epa.gov/sites/production/files/2018-01/documents/potablereusecompendium_3.pdf
- Lawrence Berkeley National Laboratory, “2024 United States Data Center Energy Usage Report”  
  https://eta-publications.lbl.gov/publications/2024-lbnl-data-center-energy-usage-report

採用した値、基準日、モデルへ採用しなかった情報、解釈上の留保は[docs/sources.md](docs/sources.md)に記載しています。合成値と仮定値は[docs/assumptions.md](docs/assumptions.md)へ分離しています。

## 同梱した観測値の来歴と利用条件

`data/observed/`のCSVは、次の公表表から数値を転記し、機械可読な列へ整形したものです。原ページ、画像、ロゴ、文章は複製していません。

- 我孫子の日降水量: 気象庁ホームページを基に本プロジェクトでCSV化。気象庁コンテンツは、個別表示がない限り[公共データ利用規約（第1.0版）に準拠する利用条件](https://www.jma.go.jp/jma/kishou/info/coment.html)です。出典と加工を明記します。
- 利根川上流9ダムの10日値: 独立行政法人水資源機構ホームページを基に本プロジェクトでCSV化。同機構の[コンテンツ利用条件](https://www.water.go.jp/honsya/honsya/policy/copyright/index.html)は出典・加工の表示を求め、政府標準利用規約2.0準拠かつCC BY 4.0互換と説明しています。
- 北総浄水場の48 ML等: 千葉県ページと事業年報にある数値事実を出典付きで参照しました。千葉県の[リンク・著作権等の方針](https://www.pref.chiba.lg.jp/homepage/about-site/link.html)に従い、県の文章、表、図、写真をリポジトリへ転載していません。

Apache License 2.0は本プロジェクト独自のコード、文書、データ整形・構成に適用します。第三者提供元の権利、個別利用条件、商標等を変更したり再許諾したりするものではありません。各CSVの出典、期間、単位、変換内容、SHA-256は[`data/observed/README.md`](data/observed/README.md)と生成時の設定・サマリーに記録します。

## 第三者コードと依存関係

- Python実行時依存: なし（標準ライブラリのみ）
- 同梱する第三者ソースコード: なし
- 同梱するモデルまたはモデル重み: なし
- 外部CDNから読み込むゲーム画面依存: なし
- 外部生成ツール: VOICEVOX（本体・音声モデルは非同梱）

将来、第三者コード、フォント、アイコン、データセット、モデルなどを同梱する場合は、その名称、入手元、バージョン、ライセンス、変更内容、必要な著作権表示をこの文書と、必要に応じて`LICENSE`または`NOTICE`へ追加してください。
