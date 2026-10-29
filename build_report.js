const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, HeadingLevel, BorderStyle, WidthType, ShadingType,
  ImageRun, PageBreak, LevelFormat, TableOfContents, Footer, PageNumber
} = require("docx");

const G = "C:/Users/2u8a/src/claude_code/mizuho-basuroke/graphs";
const FONT = "Yu Gothic";

// ---- PNG サイズ読み取り（ヘッダ 16-23 バイト）----
function pngSize(p) {
  const b = fs.readFileSync(p);
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
}
function fig(name, caption) {
  const p = `${G}/${name}.png`;
  const { w, h } = pngSize(p);
  const aspect = w / h;
  const targetW = aspect >= 1.8 ? 624 : 400;      // 横長は幅広、正方形は控えめ
  const width = targetW, height = Math.round(targetW * h / w);
  const out = [new Paragraph({
    alignment: AlignmentType.CENTER, spacing: { before: 120 },
    children: [new ImageRun({
      type: "png", data: fs.readFileSync(p),
      transformation: { width, height },
      altText: { title: name, description: caption || name, name: name }
    })]
  })];
  if (caption) out.push(new Paragraph({
    alignment: AlignmentType.CENTER, spacing: { after: 200 },
    children: [new TextRun({ text: caption, size: 18, italics: true, color: "555555" })]
  }));
  return out;
}
function h1(t) { return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)] }); }
function h2(t) { return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] }); }
function p(t, opt = {}) { return new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text: t, ...opt })] }); }
function bullet(t) { return new Paragraph({ numbering: { reference: "b", level: 0 }, spacing: { after: 60 }, children: [new TextRun(t)] }); }

// ---- テーブル ----
const bd = { style: BorderStyle.SINGLE, size: 1, color: "BBBBBB" };
const borders = { top: bd, bottom: bd, left: bd, right: bd };
function cell(text, w, { head = false, align = AlignmentType.LEFT } = {}) {
  return new TableCell({
    borders, width: { size: w, type: WidthType.DXA },
    shading: head ? { fill: "D6E4F0", type: ShadingType.CLEAR } : undefined,
    margins: { top: 60, bottom: 60, left: 120, right: 120 },
    children: [new Paragraph({ alignment: align, children: [new TextRun({ text: text, bold: head, size: 20 })] })]
  });
}
function table(widths, rows) {
  return new Table({
    width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA },
    columnWidths: widths,
    rows: rows.map((r, i) => new TableRow({
      children: r.map((c, j) => cell(String(c), widths[j], { head: i === 0, align: j === 0 ? AlignmentType.LEFT : AlignmentType.RIGHT }))
    }))
  });
}

const children = [];

// ===== 表紙 =====
children.push(
  new Paragraph({ spacing: { before: 2600, after: 200 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "瑞穂町コミュニティバス", bold: true, size: 40 })] }),
  new Paragraph({ spacing: { after: 600 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "バスロケーションシステム 実証実験（PoC）報告書", bold: true, size: 36 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 },
    children: [new TextRun({ text: "実証期間：2026年4月23日 〜 5月30日（1台構成／Raspberry Pi 4 + Firebase）", size: 24 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 },
    children: [new TextRun({ text: "対象：系統1 元狭山コース／系統2 元狭山・長岡コース／系統4 ふれあいセンターコース", size: 22 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 800 },
    children: [new TextRun({ text: "2026年7月", size: 24 })] }),
  new Paragraph({ children: [new PageBreak()] })
);

// ===== 目次 =====
children.push(h1("目次"),
  new TableOfContents("目次", { hyperlink: true, headingStyleRange: "1-2" }),
  new Paragraph({ children: [new PageBreak()] }));

// ===== はじめに =====
children.push(h1("はじめに（本報告書の目的と前提）"));
children.push(p("本報告書は、瑞穂町コミュニティバスを対象に実施したバスロケーションシステムの実証実験（PoC）の結果をまとめたものである。清瀬市等で採用される「GPS → GTFS-Realtime生成 → 地図サービスへの提供」と同等の技術要件を、1台構成で低コストに実現できるかを検証した。"));
children.push(p("実証は2026年4月23日から5月30日にかけて、車両1台（Raspberry Pi 4 + GPS + SIM、バックエンドはFirebase）で行った。得られた全GPSログをBigQueryに集約し、後述の観点から分析した。"));
children.push(h2("用語：2種類の遅延データ"));
children.push(p("本報告書では、遅延・位置の分析に2系統のデータを用いる。両者を併記することで、システムが「実際にどれだけ正しく配信できていたか」を評価できる。"));
children.push(bullet("正解データ：走行後に、全GPS軌跡から後知恵で独立に作成した「正解の便推定」。実証当時のリアルタイム判定ロジックを一切流用せず、幾何（点→経路投影）と時刻表のみで作るため、システム評価の基準（真値）として用いる。"));
children.push(bullet("運行データ：実証当時にシステムがリアルタイムに配信していた値（GTFS-Realtimeの到着遅延など）。利用者に実際に届いていた情報である。"));
children.push(p("なお本PoCは1台構成のため、同時刻に複数系統を観測することはできない。また実証期間は約5週間であり、日数・天候日数などの母数には限界がある。以降、統計値は外れ値に強い中央値を基本とし、平均値は外れ値の影響を見る補助として併記する。", { italics: true, size: 20, color: "555555" }));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ===== 1. コスト =====
children.push(h1("1. コスト（イニシャル／ランニング）"));
children.push(p("既存の民間バスロケーションシステムは、初期費用が数百万円規模となることが一般的である。本PoCの実費は下表のとおりで、桁違いに安価に構築・運用できることを実証した。"));

children.push(h2("1.1 イニシャルコスト（機材・1台あたり）"));
children.push(table([6360, 3000], [
  ["品目", "金額（円）"],
  ["マイコン（Raspberry Pi 4 ほか）", "11,550"],
  ["SIMドングル", "6,480"],
  ["GPSモジュール", "3,750"],
  ["DCDCコンバーター", "1,559"],
  ["microSD", "980"],
  ["変換ケーブル（C to A）", "799"],
  ["合計", "25,118"],
]));
children.push(p("車両1台あたりの機材費は約2.5万円。一般的なバスロケの初期費用（数百万円〜）と比べて2桁小さい。", { size: 20 }));

children.push(h2("1.2 ランニングコスト（月額・1台あたり）"));
children.push(table([4680, 2340, 2340], [
  ["項目", "内訳", "月額（円）"],
  ["SIM通信（SORACOM）", "プランD 500MB + バンドル", "約 850"],
  ["Firebase（クラウド）", "Firestoreストレージ + 税ほか", "310"],
  ["合計", "", "約 1,160"],
]));
children.push(p("Firebaseの課金内訳（実請求・2026年6月）を見ると、GPSの書き込み（Entity Writes）・読み取り（Read Ops）はいずれも無料枠内で0円であり、課金の主体はログ蓄積分のストレージ（約281円）と税である。すなわちデータ処理そのものはほぼ無償で、運用費の大半は通信SIMが占める。"));

children.push(...fig("cost_firestore_writes_by_day",
  "図1-1. 1台のFirestore書込回数（日別）と無料枠。平均約4,395件/日・ピーク4,930件/日で、無料枠20,000件/日の約25%に収まる。"));
children.push(p("書き込みは無料枠（20,000件/日）に対し1台でピークでも約25%であり、書き込み課金は発生しない。この余裕から、単純計算で約4台までは無料枠内で運用でき、ランニングの追加費用は主にSIM通信費のみとなる見込みである。"));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ===== 2. 運行データの分析 =====
children.push(h1("2. 運行データの分析"));

children.push(h2("2.1 時間帯ごとの遅延"));
children.push(...fig("figures_fig3_delay_by_hour",
  "図2-1. 時間帯別の到着遅延。午後から夕方にかけて遅延が増える傾向。"));
children.push(p("朝（6時台）は遅延が小さい（中央値およそ95秒）一方、午後から夕方にかけて遅延が増大し、17時台・12時台では5分超の割合が3〜4割に達する。日中の交通量増加に伴い遅延が蓄積する典型的なパターンである。"));
children.push(p("ただし、時間帯によって運行される系統の構成は一様ではない。この偏りによる交絡を確認するため、路線別に分けて時間帯別の遅延を集計し、あわせて時間帯ごとの運行（観測）便数も示した（下図）。"));
children.push(...fig("figures_fig3b_delay_by_hour_route",
  "図2-1b. 路線別・時間帯別の到着遅延（上段・正解データ中央値）と、時間帯ごとの観測便数（下段）。白丸は便数僅少の参考値。"));
children.push(p("下段のとおり、路線ごとに運行時間帯が明確に偏っている。元狭山コース（系統1）は早朝から夜（20時台）まで運行し、しかも3系統の中で最も遅れやすい。一方、元狭山・長岡コース（系統2）は主に朝〜夕方前（18時台まで）で遅延は相対的に小さく、ふれあいセンターコース（系統4）は日中のみ・便数僅少である。"));
children.push(p("したがって、合算図（図2-1）が示す「夕方に大きく遅れる」傾向は、夕方〜夜の時間帯を最も遅れやすい系統1がほぼ独占していることの影響（構成の偏り）を多分に含む。実際、夕方前まで運行する系統2の遅延はおおむね1〜2分で頭打ちである。"));
children.push(p("一方で、系統1単独で見ても早朝（6時台・中央値約1.7分）から夜（20時台・約3.0分）へ遅延が増える実質的な右肩上がりがあり、時間帯そのものの影響（日中の交通量増加）も確かに存在する。結論として『午後〜夕方に遅くなる』は、①系統1が夕方帯を占める構成の偏り と ②系統1内部の時間帯効果 の両方が重なった結果と解釈すべきで、全系統に一律に当てはめるのは適切でない。"));

children.push(h2("2.2 天候（雨天時）の影響"));
children.push(...fig("figures_fig4_weather_weekday",
  "図2-2. 天候別・曜日別の遅延。"));
children.push(p("素の比較では雨天日と晴天日で遅延・精度・GPS品質に大きな差は見られなかった。ただし本実証における雨天日は5日程度と極端に少なく、しかも初期の雨天日がアルゴリズム不安定期（後述のv3導入直後）と重なる交絡があるため、この結果は統計的に信頼できるデータとは言い難い。「雨天でも悪化が確認されなかった」という参考情報にとどめ、断定は避けるべきである。", {}));
children.push(p("アルゴリズム安定期（5月10日以降）に限定した比較でも、便推定精度・GPS品質・遅延に実質的な差は見られず、少なくとも観測範囲では雨に対して頑健であった。ただし母数の小ささは前記のとおりである。"));

children.push(h2("2.3 曜日ごとの違い"));
children.push(p("曜日別では土曜の遅延がやや大きく、到着遅延が2分を超える割合が最も高かった（およそ54%）。休日ダイヤ・交通状況の違いによると考えられる。"));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ===== 2.4 各コースの遅延分析 =====
children.push(h2("2.4 各コースの停留所別 到着遅延"));
children.push(p("各コースについて、停留所ごとの到着遅延を「運行順（循環）」に並べて示す。X軸は箱根ケ崎駅を発着とする1周で、往路→折り返し→復路（(復)表記）→駅着の順である。往路と復路で同じ道を通る停留所は別々に集計しており、往路で遅れが蓄積し復路で回復する傾向が読み取れる。"));
children.push(p("各コースで、正解データ（真値）と運行データ（システム配信値）、それぞれの中央値・平均値を併載する。あわせて、道路形状に沿って遅延を色分けした地図（往路・復路を進行方向左へ14mずらして自動分離）を示す。"));

const routes = [
  { id: "1", name: "元狭山コース（系統1）" },
  { id: "2", name: "元狭山・長岡コース（系統2）" },
  { id: "4", name: "ふれあいセンターコース（系統4）" },
];
for (const r of routes) {
  children.push(new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 200 }, children: [new TextRun(`2.4.${r.id === "1" ? "1" : r.id === "2" ? "2" : "3"} ${r.name}`)] }));
  children.push(...fig(`route_delay_gt_gradient_road_route${r.id}`,
    `図2-4-${r.id}a. ${r.name} 道路形状に沿った到着遅延グラデーション（正解データ・平均値）。緑=定時／赤=大遅延。往路・復路は左14mずらしで分離。`));
  children.push(...fig(`reliability_delay_by_stop_route${r.id}_median`,
    `図2-4-${r.id}b. ${r.name} 停留所別 到着遅延（正解データ・中央値）。`));
  children.push(...fig(`reliability_delay_by_stop_route${r.id}_mean`,
    `図2-4-${r.id}c. ${r.name} 停留所別 到着遅延（正解データ・平均値）。`));
  children.push(...fig(`reliability_delay_by_stop_op_route${r.id}_median`,
    `図2-4-${r.id}d. ${r.name} 停留所別 到着遅延（運行データ・中央値）。`));
  children.push(...fig(`reliability_delay_by_stop_op_route${r.id}_mean`,
    `図2-4-${r.id}e. ${r.name} 停留所別 到着遅延（運行データ・平均値）。`));

  if (r.id === "1") {
    children.push(p("元狭山コースの停留所別遅延（実測値）では、往路で遅れが蓄積し（瑞穂二本木で3〜4分程度がピーク）、折り返し以降の復路では概ね定時付近に回復する様子が見て取れる。"));
    children.push(p("一方で、折り返し地点である「瑞穂二本木（復）」では遅れがマイナス、すなわち早着・早発しているように出力された。ここは折り返し地点であり、システム側で何らかのエラーが起きているのではないかと考えられる。事実、平均値ではこのマイナスが顕著であるのに対し、中央値では−0.3分程度に収まっている。つまり一部のエラー的な外れ値によって平均値全体が引っ張られている可能性がある。周囲の停留所の数値がプラス（遅れ）であることからも、折り返し地点では依然として課題が残ったことがわかる。"));
    children.push(p("この現象はシステムの欠点として第3章で改めて分析する。", { italics: true, size: 20, color: "555555" }));
  } else if (r.id === "2") {
    children.push(p("元狭山・長岡コースも往路で遅れが増え、復路で落ち着く同様の傾向を示した。正解データと運行データの形状はよく一致しており、システムが遅延の地理的パターンをおおむね正しく捉えていたことがわかる。"));
  } else {
    children.push(p("ふれあいセンターコースは停留所数が少なく（実質的な集計対象が数点）、参考扱いとする。傾向としては大きな遅延は見られなかった。"));
  }
  children.push(new Paragraph({ children: [new PageBreak()] }));
}

// ===== 2.5 終点駅到着 =====
children.push(h2("2.5 終点（箱根ケ崎駅）到着時の遅延について"));
children.push(p("終点である箱根ケ崎駅への「到着遅延」を素直に集計すると、全便がおよそ6分の早着として出力される。しかしこれは実際に6分早く着いているのではなく、測定上の性質による見かけの値である。"));
children.push(p("理由は、バスが終点駅に到達すると便（トラッキング）が終了し、GPS記録もそこで止まるためである。結果として、駅の30〜50m手前・予定時刻の6〜7分前の点が最後の記録となる。言い換えれば、終点駅の時刻表には余裕（パディング）が設けられており、実運行は予定より数分早く駅圏へ戻っていることを示している。"));
children.push(p("したがって終点駅そのものの到着遅延は測定困難であり、実質的には終点手前の停留所（例：瑞穂町役場で中央値+1.1分程度）が「駅到着直前の遅延」を表す。運行データ（システム配信値）では終点付近はほぼ0分で配信されており、利用者に大きな早着が誤って伝わることはなかった。"));
children.push(p("この「終点での見かけの早着」は、遅延の多くが運行の失敗ではなく時刻表側の余裕によることを示す好例であり、ダイヤ設計上の示唆（第6章）につながる。", { italics: true, size: 20, color: "555555" }));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ===== 3. システムの欠点と分析 =====
children.push(h1("3. システムの欠点と分析"));
children.push(h2("3.1 折り返し地点での便推定エラー（元狭山コース）"));
children.push(p("第2章で述べたとおり、元狭山コースの折り返し地点「瑞穂二本木（復）」で、到着遅延がマイナス（早着・早発）として出力される現象が確認された。周囲の停留所がプラス（遅れ）であるにもかかわらず、この1点だけが逆符号となる。"));
children.push(p("平均値では大きくマイナスに振れ、中央値では−0.3分程度に収まることから、原因は恒常的な早発ではなく、一部の走行での便推定・到着検出の誤り（外れ値）が平均を引っ張っているものと考えられる。折り返し地点は、往路と復路が近接し、同じ地点を短時間に二度通過するため、システムが「どちらの脚か」「どの停留所への到着か」を取り違えやすい構造的な難所である。"));
children.push(h2("3.2 要因の分析"));
children.push(bullet("往復近接：折り返し前後は往路と復路の経路・停留所が近接し、進行方向の取り違えが起きやすい。"));
children.push(bullet("短時間の二度通過：同一（または至近）地点を短時間で二度通るため、到着イベントの対応付けが乱れやすい。"));
children.push(bullet("終端の余裕ダイヤ：折り返し・終端には時刻表上の余裕があり、予定と実態のズレが早着方向に出やすい。"));
children.push(p("これらは実運用中のPDCA（第5章）でも順次対処し、シーケンシャルトラッキングやトポロジー型シーケンス管理の導入で大幅に改善した。ただし折り返し地点については本実証終了時点でも完全には解消しておらず、課題として残った。"));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ===== 4. GPSの精度 =====
children.push(h1("4. GPSの精度"));
children.push(...fig("heatmap_gps_accuracy",
  "図4-1. GPS精度ヒートマップ（全系統・4〜5月）。淡色=良好／赤=誤差が大きい（GPSが弱い）。"));
children.push(p("今回使用した機材のGPS精度は全体として良好であり、推定誤差は平均およそ33m、50mを超える点は全体の6.6%に過ぎなかった。運行ルートの大半は淡色（誤差が小さい）で表示されている。"));
children.push(p("その上で、上図のとおり丘陵地の一部区間には局所的にGPSが弱くなる地点（赤いスポット）が見られた。停留所別に見ても、ふれあいセンターや飯能街道、病院前など丘陵・樹木の多い地点で相対的に誤差が大きい傾向があり、地形の影響がうかがえる。"));
children.push(p("総括すると、本機材では「場所による大きな精度差はなく、実用上おおむね安定して測位できた」一方で、「丘陵地の数地点に局所的な弱点が存在する」といえる。これらの弱点地点は、複数台展開時にアンテナ配置や測位方式を検討する際の参考になる。"));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ===== 5. 便推定精度とPDCAの記録 =====
children.push(h1("5. 便推定精度と開発PDCAの記録"));
children.push(h2("5.1 便推定精度（正解照合）"));
children.push(p("走行後に作成した正解データと、実証当時のリアルタイム便推定を点単位で照合し、精度を算出した（運行中・高信頼の点のみで評価）。全期間の便推定精度は79.4%で、アルゴリズムの改修に伴い期間ごとに大きく変化した。"));
children.push(table([3600, 2880, 2880], [
  ["期間（版）", "便推定精度", "系統推定精度"],
  ["pre-v3 再推定（4/23-4/30）", "75.6%", "83.9%"],
  ["v3 便固定（5/1-5/8）", "68.4%", "76.4%"],
  ["v3.2 始発保留（5/9）", "86.9%", "93.1%"],
  ["v3.3 topology（5/10-）", "84.2%", "91.6%"],
]));
children.push(p("便固定型v3の導入直後（5/1〜5/5）に精度が一度低下したが（新たな不具合の発生）、その後の修正とv3.2・v3.3で85〜93%へ回復・安定した。安定版では実用水準の精度に達している。"));

children.push(h2("5.2 開発PDCAの記録（公開サイトより）"));
children.push(p("実証期間中、課題の発見（Check）→改善設計（Plan）→実装・デプロイ（Do）→効果確認（Action）のサイクルを短期間で繰り返した。主な経緯は以下のとおり（公開済みの開発記録サイトより要約）。"));
const pdca = [
  ["4/30", "Check / Action", "毎受信で便を再推定する構造の限界を発見。車両状態を5状態で管理する「便固定型」v3へアーキテクチャを刷新。"],
  ["5/2", "Check / Do", "始発が駅以外の便で確定が遅れる／折返し便を回送と誤判定する問題を確認。距離許容を400m→1000mへ拡大し、終点距離に基づく早期終了を実装。"],
  ["5/2", "Check", "出発前待機が「早着」として計算される／往復同一道路で進行方向を取り違え大遅延を誤算定する問題を発見。"],
  ["5/6-5/9", "Plan / Do", "早着ゼロクランプ（始発保留）と、通過済み停留所の順序を記憶するシーケンシャルトラッキングを設計・実装（v3.2）。往復重複でも逆走・誤スナップを排除。"],
  ["5/10", "Do / Check", "停留所進捗をバックエンドで生成し、即時終着判定を追加（v3.3）。管理画面を縦型路線図UIへ刷新。実便で動作確認。"],
  ["5/11", "Check / Action", "GTFSの通過順序に欠番がある便でトラッキングが停止する問題を発見。数値加算をやめ、便ごとの専用配列によるトポロジー型シーケンス管理へ移行し、不規則な実データへのフォールトトレランスを確立。"],
];
children.push(table([1400, 2200, 5760],
  [["日付", "区分", "内容"]].concat(pdca)));
children.push(p("この記録は、他自治体・他事業者がバスロケを導入する際に直面しうる実データ起因の課題（往復重複、シーケンス欠番、折返し・終端処理）と、その解決手法を具体的に示すものであり、横展開の参考資料として有用である。"));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ===== 6. まとめと提案 =====
children.push(h1("6. まとめと町への提案"));
children.push(h2("6.1 まとめ"));
children.push(bullet("コスト：機材費 約2.5万円/台、ランニング 約1,160円/月・台（うちFirebaseは約310円、書込・読取は無料枠内で0円）。一般的なバスロケ（初期数百万円〜）に対し桁違いに安価に構築・運用できることを実証した。"));
children.push(bullet("精度：安定版で便推定 85〜93%。実用水準に到達。正解データとの照合という客観指標で裏付けた。"));
children.push(bullet("運行示唆：遅延は日中〜夕方に蓄積。往路で遅れ、復路〜終端で回復。終端の見かけの早着は時刻表の余裕（パディング）による。"));
children.push(bullet("GPS：全体は良好（>50mは6.6%）だが、丘陵地に局所的な弱点。"));
children.push(bullet("課題：折り返し地点の便推定に外れ値的な誤りが残存。"));
children.push(h2("6.2 提案"));
children.push(bullet("ダイヤ検討：終端・折返し付近の余裕（パディング）区間を可視化できたため、時刻表の見直し（実態に近い所要時間への調整）の基礎資料として活用できる。"));
children.push(bullet("折り返し処理の改善：残存する折り返し地点の便推定エラーは、往復の判定強化・到着イベントの対応付け見直しでさらに改善余地がある。"));
children.push(bullet("GPS弱点への対応：丘陵地の弱点地点は、複数台展開時のアンテナ配置・測位方式の検討材料とする。"));
children.push(bullet("スケール：書込は無料枠内で約4台まで対応可能。台数拡大時の主コストはSIM通信費であり、費用の見通しが立てやすい。"));
children.push(p("以上より、本PoCは「低コストで実用水準のバスロケーションが1台構成でも実現可能」であることを示した。次年度以降の発注仕様策定・複数台展開の判断材料として活用されたい。", { bold: true }));

// ===== ドキュメント =====
const doc = new Document({
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, font: FONT, color: "1F3864" },
        paragraph: { spacing: { before: 240, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 25, bold: true, font: FONT, color: "2E5496" },
        paragraph: { spacing: { before: 180, after: 100 }, outlineLevel: 1 } },
    ]
  },
  numbering: { config: [
    { reference: "b", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 600, hanging: 300 } } } }] }
  ] },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ text: "", size: 18 }), new TextRun({ children: [PageNumber.CURRENT], size: 18 })] })] }) },
    children
  }]
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync("瑞穂町バスロケPoC報告書.docx", buf);
  console.log("written 瑞穂町バスロケPoC報告書.docx  bytes=" + buf.length);
});
