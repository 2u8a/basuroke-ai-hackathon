const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, HeadingLevel, BorderStyle, WidthType, ShadingType,
  ImageRun, PageBreak, LevelFormat, TableOfContents, Footer, PageNumber
} = require("docx");

const G = path.join(__dirname, "..", "graphs");
const FONT = "Yu Gothic";

function pngSize(p){ const b=fs.readFileSync(p); return {w:b.readUInt32BE(16),h:b.readUInt32BE(20)}; }
function fig(name, caption){
  const p=`${G}/${name}.png`; const {w,h}=pngSize(p); const aspect=w/h;
  const tw = aspect>=1.8?624:400; const width=tw, height=Math.round(tw*h/w);
  const out=[new Paragraph({alignment:AlignmentType.CENTER, spacing:{before:120},
    children:[new ImageRun({type:"png",data:fs.readFileSync(p),transformation:{width,height},
      altText:{title:name,description:caption||name,name}})]})];
  if(caption) out.push(new Paragraph({alignment:AlignmentType.CENTER, spacing:{after:180},
    children:[new TextRun({text:caption,size:18,italics:true,color:"555555"})]}));
  return out;
}
const h1=t=>new Paragraph({heading:HeadingLevel.HEADING_1,children:[new TextRun(t)]});
const h2=t=>new Paragraph({heading:HeadingLevel.HEADING_2,children:[new TextRun(t)]});
const h3=t=>new Paragraph({spacing:{before:140,after:60},children:[new TextRun({text:t,bold:true,size:23,color:"2E5496"})]});
const p=(t,opt={})=>new Paragraph({spacing:{after:120},children:[new TextRun({text:t,...opt})]});
const lead=(b,t)=>new Paragraph({spacing:{after:100},children:[new TextRun({text:b,bold:true}),new TextRun({text:t})]});
const bullet=t=>new Paragraph({numbering:{reference:"b",level:0},spacing:{after:60},children:[new TextRun(t)]});

const bd={style:BorderStyle.SINGLE,size:1,color:"BBBBBB"};
const borders={top:bd,bottom:bd,left:bd,right:bd};
function cell(text,w,{head=false,align=AlignmentType.LEFT}={}){
  return new TableCell({borders,width:{size:w,type:WidthType.DXA},
    shading:head?{fill:"D6E4F0",type:ShadingType.CLEAR}:undefined,
    margins:{top:60,bottom:60,left:120,right:120},
    children:[new Paragraph({alignment:align,children:[new TextRun({text,bold:head,size:20})]})]});
}
function table(widths,rows,{firstColLeft=true}={}){
  return new Table({width:{size:widths.reduce((a,b)=>a+b,0),type:WidthType.DXA},columnWidths:widths,
    rows:rows.map((r,i)=>new TableRow({children:r.map((c,j)=>cell(String(c),widths[j],
      {head:i===0, align:(j===0&&firstColLeft)?AlignmentType.LEFT:(j===0?AlignmentType.LEFT:AlignmentType.RIGHT)}))}))});
}

const C=[];

// ===== 表紙 =====
C.push(
  new Paragraph({spacing:{before:2400,after:200},alignment:AlignmentType.CENTER,children:[new TextRun({text:"瑞穂町コミュニティバス",bold:true,size:40})]}),
  new Paragraph({spacing:{after:500},alignment:AlignmentType.CENTER,children:[new TextRun({text:"バスロケーションシステム 実証実験報告書",bold:true,size:34})]}),
  new Paragraph({alignment:AlignmentType.CENTER,spacing:{after:80},children:[new TextRun({text:"実証期間：2026年4月23日 〜 5月30日（1台構成／Raspberry Pi 4 + Firebase）",size:22})]}),
  new Paragraph({alignment:AlignmentType.CENTER,spacing:{after:80},children:[new TextRun({text:"対象：元狭山コース／元狭山・長岡コース／ふれあいセンターコース",size:22})]}),
  new Paragraph({alignment:AlignmentType.CENTER,spacing:{before:700},children:[new TextRun({text:"2026年7月",size:22})]}),
  new Paragraph({children:[new PageBreak()]})
);

// ===== 要約（結論） =====
C.push(h1("要約 ― 結論（先に数字で）"));
C.push(p("本実証で確認された結論を、先に数字で示す。詳細は各章に記す。"));
C.push(table([3200,6160],[
  ["項目","結果"],
  ["初期費用","約25,000円/台（一般的なバスロケは数百万円〜）"],
  ["運用費","約1,160円/月・台（SIM通信 約850円＋クラウド 約310円）。GPSの書込・読取は無料枠内で0円"],
  ["便推定精度","安定版で85〜93%（実用水準）"],
  ["GPS精度","平均約33m（誤差50m超は全体の6.6%のみ）"],
  ["遅れやすい場所","元狭山コースの駒形富士山周辺で平均約3.3分、瑞穂二本木周辺で約3.7分。日中〜夕方に蓄積"],
]));
C.push(p("結論として、低コストで実用水準のバスロケーションが1台構成でも実現可能であることを実証した。次年度以降の発注仕様や複数台展開の判断材料として活用されたい。",{bold:true}));
C.push(new Paragraph({children:[new PageBreak()]}));

// ===== 目次 =====
C.push(h1("目次"),new TableOfContents("目次",{hyperlink:true,headingStyleRange:"1-2"}),new Paragraph({children:[new PageBreak()]}));

// ===== はじめに =====
C.push(h1("はじめに（目的と前提）"));
C.push(p("瑞穂町コミュニティバスを対象に、清瀬市等で採用される「GPS → GTFS-Realtime生成 → 地図サービスへの提供」と同等の技術を、1台構成で低コストに実現できるかを検証した実証実験（以下、実証）の報告である。"));
C.push(p("実証は2026年4月23日〜5月30日、車両1台（Raspberry Pi 4 + GPS + SIM、バックエンドはFirebase）で行い、全GPSログをBigQueryに集約して分析した。"));
C.push(h3("用語：2種類のデータ"));
C.push(lead("正解データ：","走行後に、全GPS軌跡から後知恵で独立に作成した「正解の便推定」。当時のリアルタイム判定を一切流用せず、幾何と時刻表のみで作るため、評価の基準（真値）として用いる。"));
C.push(lead("運行データ：","実証当時にシステムがリアルタイムに配信していた値。利用者に実際に届いていた情報である。"));
C.push(p("各グラフの図題に、いずれのデータかを明記する。なお1台構成のため同時刻に複数系統は観測できず、実証は約5週間で天候日数などの母数には限界がある。統計値は外れ値に強い中央値を基本とし、平均値は補助として併記する。",{size:20,color:"555555"}));
C.push(new Paragraph({children:[new PageBreak()]}));

// ===== 1. コスト =====
C.push(h1("1. コスト"));
C.push(p("既存の民間バスロケは初期費用が数百万円規模となることが一般的だが、本実証の実費は下表のとおりで、桁違いに安価に構築・運用できた。"));
C.push(h3("1.1 初期費用（機材・1台あたり）"));
C.push(table([6360,3000],[
  ["品目","金額（円）"],["マイコン（Raspberry Pi 4 ほか）","11,550"],["SIMドングル","6,480"],
  ["GPSモジュール","3,750"],["DCDCコンバーター","1,559"],["microSD","980"],["変換ケーブル（C to A）","799"],["合計","25,118"],
]));
C.push(h3("1.2 運用費（月額・1台あたり）"));
C.push(table([4680,2340,2340],[
  ["項目","内訳","月額（円）"],["SIM通信（SORACOM）","プランD 500MB + バンドル","約 850"],
  ["クラウド（Firebase）","ストレージ + 税ほか","310"],["合計","","約 1,160"],
]));
C.push(p("Firebaseの実請求（2026年6月）を見ると、GPSの書込・読取はいずれも無料枠内で0円であり、課金の主体はログ蓄積分のストレージである。データ処理はほぼ無償で、運用費の大半はSIM通信費が占める。"));
C.push(...fig("cost_firestore_writes_by_day","図1-1. 1台のFirestore書込回数（日別）と無料枠。平均約4,395件/日・ピーク4,930件/日で、無料枠20,000件/日の約25%に収まる。"));
C.push(p("書込は無料枠に対しピークでも約25%であり、単純計算で約4台までは無料枠内で運用できる。台数拡大時の追加費用は主にSIM通信費となる見込みである。"));
C.push(new Paragraph({children:[new PageBreak()]}));

// ===== 2. 結果 =====
C.push(h1("2. 結果"));

C.push(h2("2.1 便推定精度"));
C.push(p("走行後に作った正解データと、当時のリアルタイム便推定を点単位で照合して精度を算出した（運行中・高信頼の点のみ）。全期間で79.4%、アルゴリズム改修に伴い期間ごとに大きく変化した。"));
C.push(table([3600,2880,2880],[
  ["期間（版）","便推定精度","系統推定精度"],
  ["pre-v3 再推定（4/23-4/30）","75.6%","83.9%"],
  ["v3 便固定（5/1-5/8）","68.4%","76.4%"],
  ["v3.2 始発保留（5/9）","86.9%","93.1%"],
  ["v3.3（5/10-）","84.2%","91.6%"],
]));
C.push(p("便固定型v3の導入直後（5/1〜5/5）に一度低下したが、その後の修正で安定版は85〜93%へ回復した。実用水準の精度に達している（改修の経緯は第4章）。"));

C.push(h2("2.2 遅延 ― どこで・どれくらい"));
C.push(lead("元狭山コース：","駒形富士山周辺で平均約3.3分、瑞穂二本木周辺で約3.7分の遅れがピーク。往路で遅れが蓄積し、折り返し以降の復路で定時付近まで回復する。"));
C.push(lead("元狭山・長岡コース：","運行の進行とともに遅延が滑らかに蓄積して安定し、大きな突発は見られない。マイナスに転じるのは終点のみである。"));
C.push(lead("ふれあいセンターコース：","停留所数・データ数が少なく、値には検出のばらつき等が含まれうるため参考扱い。大きな遅延は見られない。"));
C.push(p("各コースの停留所別到着遅延を運行順（循環）に示す。X軸は箱根ケ崎駅発着の1周（往路→折返し→復路（(復)表記）→駅着）。往復で同じ道を通る停留所は別々に集計している。各コースで正解データ・運行データの中央値/平均値、および道路形状に沿った遅延マップ（往復を左14mずらして分離）を併載する。"));

const routes=[
  {id:"1",name:"元狭山コース",note:"往路で遅れが蓄積し（瑞穂二本木でピーク約3.7分）、折り返し以降の復路で定時付近まで回復する。復路の瑞穂二本木（復）は当初マイナス（見かけの早着）が出たが、これは正解データ生成側の検出上の課題で、既に是正済みである（原因と対処は第3章）。"},
  {id:"2",name:"元狭山・長岡コース",note:"運行の進行とともに遅延が滑らかに蓄積して安定しており、突発的な遅延はない。正解データと運行データの形状はよく一致し、システムが遅延の地理的パターンを正しく捉えていたことがわかる。"},
  {id:"4",name:"ふれあいセンターコース",note:"停留所数・データ数が少なく参考扱い。大きな遅延は見られない。"},
];
for(const r of routes){
  C.push(h3(`2.2.${r.id==="1"?"1":r.id==="2"?"2":"3"} ${r.name}`));
  C.push(...fig(`route_delay_gt_gradient_road_route${r.id}`,`${r.name}：道路形状に沿った到着遅延マップ（正解データ・平均値）。緑=定時／赤=大遅延。往路・復路は左14mずらしで分離。`));
  C.push(...fig(`reliability_delay_by_stop_route${r.id}_median`,`${r.name}：停留所別 到着遅延（正解データ・中央値）。`));
  C.push(...fig(`reliability_delay_by_stop_route${r.id}_mean`,`${r.name}：停留所別 到着遅延（正解データ・平均値）。`));
  C.push(...fig(`reliability_delay_by_stop_op_route${r.id}_median`,`${r.name}：停留所別 到着遅延（運行データ・中央値）。`));
  C.push(...fig(`reliability_delay_by_stop_op_route${r.id}_mean`,`${r.name}：停留所別 到着遅延（運行データ・平均値）。`));
  C.push(p(r.note));
  C.push(new Paragraph({children:[new PageBreak()]}));
}

C.push(h2("2.3 遅延 ― いつ（時間帯）"));
C.push(...fig("figures_fig3_delay_by_hour","図2-3a. 時間帯別の到着遅延（全系統）。午後〜夕方に増える傾向。"));
C.push(p("朝（6時台）は中央値およそ95秒と小さいが、午後〜夕方に増大し、17時台・12時台では5分超の割合が3〜4割に達する。"));
C.push(...fig("figures_fig3b_delay_by_hour_route","図2-3b. 路線別・時間帯別の到着遅延（上段・正解データ中央値）と観測便数（下段）。"));
C.push(p("ただし運行時間帯は路線で偏る（下段）。元狭山コースは早朝〜夜まで運行し最も遅れやすく、元狭山・長岡コースは夕方前まで・遅延は小さい。したがって合算の「夕方に大きく遅れる」傾向は、夕方帯を最も遅れやすい元狭山コースが占める構成の偏りを多分に含む。"));
C.push(p("一方、元狭山コース単体でも早朝（約1.7分）から夜（約3.0分）へ遅延が増える実質的な右肩上がりがあり、時間帯そのものの影響も存在する。結論として『午後〜夕方に遅くなる』は、構成の偏りと時間帯効果の両方が重なった結果で、全系統に一律には当てはめられない。"));

C.push(h2("2.4 天候・曜日"));
C.push(...fig("figures_fig4_weather_weekday","図2-4. 天候別・曜日別の遅延。"));
C.push(p("素の比較では雨天日と晴天日で遅延に大きな差はなかった。ただし雨天日は5日程度と少なく、初期の雨天日がアルゴリズム不安定期と重なる交絡もあるため、統計的に信頼できるデータとは言い難い。「雨天でも悪化が確認されなかった」という参考情報にとどめる。曜日別では土曜の遅延がやや大きく、2分超の割合が最も高かった（約54%）。"));

C.push(h2("2.5 GPS精度"));
C.push(...fig("heatmap_gps_accuracy","図2-5. GPS精度ヒートマップ（全系統・4〜5月）。淡色=良好／赤=誤差が大きい。"));
C.push(p("GPS精度は全体として良好で、推定誤差は平均およそ33m、50mを超える点は全体の6.6%にとどまった。運行ルートの大半は淡色（良好）である。"));
C.push(p("その上で、丘陵地の一部区間には局所的にGPSが弱くなる地点（赤いスポット）が存在する。停留所別でも、ふれあいセンター・飯能街道・病院前など丘陵・樹木の多い地点で相対的に誤差が大きく、地形の影響がうかがえる。総じて「場所による大きな精度差はなく実用上おおむね安定して測位できたが、丘陵地に局所的な弱点がある」といえる。"));
C.push(new Paragraph({children:[new PageBreak()]}));

// ===== 3. 問題点 =====
C.push(h1("3. 問題点"));

C.push(h2("3.1 折り返し地点の見かけの早着（正解データ生成側の課題）"));
C.push(p("元狭山コースの折り返し地点「瑞穂二本木（復）」で、当初の正解データでは到着遅延がマイナス（早着・早発のように見える値）が出力された。早発は通常の運行では想定しにくく、周囲の停留所がすべてプラス（遅れ）であることからも、実運行の早発ではなく、真値を作る正解データ生成プログラムの到着検出の問題であった。"));
C.push(h3("原因：90度コーナーの先の停留所"));
C.push(p("到着検出は「バスが停留所の座標に最も近づいた瞬間（直線距離の谷）」を到着時刻とみなす。瑞穂二本木（復）は進行方向に対して約90度曲がった先にあるため、バスが角を曲がる手前の方が停留所への直線距離が近くなり、実際の到着より約2分手前に「谷」ができる。当時の検出はこの最初の谷を拾っていたため、早く出ていた。"));
C.push(p("データもこれを裏付ける。マイナスと算出された検出は、停留所への最接近距離が中央値約120mと、正常な検出（約44m）より明確に大きい。もしGPSのブレが原因なら両者の距離は同程度になるはずで、これは「バスが停留所の近くを通れていない＝配置の幾何」が原因であることを示す。GPS誤差（平均約33m）は時刻に換算すると数秒〜十数秒にすぎず、分オーダーの早着の主因ではない。"));
C.push(h3("対処：検出方法の改善"));
C.push(p("到着検出を、時間窓の中で「最初の谷」ではなく本当の最接近点を採るように改めた。最接近が平坦に続く（＝停留所の横をしばらく走る）場合はその中で予定時刻に最も近い点を、折り返しのように近づく箇所が複数ある場合は予定時刻に最も近い方を選ぶ。これにより瑞穂二本木（復）の到着遅延は平均−3.1分から+1.7分（中央値−0.3→+0.6分）へ是正され、周囲と整合する妥当な値になった。本書のグラフはすべて是正後の正解データに基づく。"));
C.push(p("なお、実運用システム（リアルタイム側）の折り返し処理も、実証中の改善（第4章）で大幅に安定化している。今回の課題は事後に真値を作る正解データ生成側のものであり、上記で是正した。",{size:20,color:"555555"}));

C.push(h2("3.2 終点の見かけの早着"));
C.push(p("終点（箱根ケ崎駅）の到着遅延を集計すると、全コース共通でマイナス（早着）が出る。現時点では、①時刻表のパディング（終点付近に設けられた余裕）と、②終点特有の検出（終点でその便の記録が終わり、後続のGPSが無いため到着時刻を正確に定めきれない）の、両方が影響している可能性がある。"));
C.push(p("いずれにせよ終点そのものの到着遅延は測定が難しい。実測では記録が駅の30〜50m手前・予定の6〜7分前で途切れており、これは終点の予定時刻に余裕があり、実運行は予定より数分早く駅圏へ戻っていることを示す。パディングは、駅への到着が大きく遅れることを防ぐ設計上の工夫でもある。"));
C.push(lead("補足（地図アプリの案内）：","この余裕は地図アプリの経路検索にも表れる。終点手前区間の予定所要が徒歩並み〜それ以下（元狭山・長岡コースは約699mに予定11分＝時速約3.8km・徒歩約9分、元狭山コースは約1,064mに予定14分＝時速約4.6km・徒歩約13.6分）のため、アプリが「一つ手前で降りて歩いた方が早い」と案内する現象が起こりうる。運行が遅いのではなく、時刻表の余裕を反映したものである。"));
C.push(new Paragraph({children:[new PageBreak()]}));

// ===== 4. 改善（PDCA） =====
C.push(h1("4. 改善 ― 開発の記録（PDCA）"));
C.push(p("実証中、課題の発見→改善設計→実装→効果確認のサイクルを短期間で繰り返し、便推定精度を85〜93%まで引き上げた。主な経緯は以下のとおり（専門的な処理は平易に言い換えて記す）。"));
C.push(table([1300,2000,6060],[
  ["日付","区分","内容"],
  ["4/30","課題→改善","毎回便を推定し直す方式の限界を確認。車両の状態を5段階で管理する「便固定型」に作り直した。"],
  ["5/2","課題→対応","始発が駅以外の便で確定が遅れる／折返しを回送と誤判定する問題に対し、許容距離を広げ、終点手前で早めに便を終える処理を追加。"],
  ["5/6-5/9","計画→実装","出発前の待機を早着と誤計算しないよう遅延を0で止める処理と、通過済みの停留所を記録して探索範囲を絞る方式を導入。"],
  ["5/10","実装→確認","停留所ごとの進捗をサーバ側で生成し、終点到着で即座に便を終える処理を追加。管理画面を縦型の路線図に刷新。"],
  ["5/11","課題→改善","時刻表の停留所番号に欠番がある便で停止する問題を、番号の連番ではなく「通過した順番を記録する方式」に変えて解消。ダイヤ変更や停留所の増減にも対応可能になった。"],
]));
C.push(p("この記録は、他自治体・他事業者がバスロケを導入する際に直面しうる実データ起因の課題（往復重複、番号の欠番、折返し・終端処理）と、その解決手法を具体的に示すもので、横展開の参考になる。"));
C.push(new Paragraph({children:[new PageBreak()]}));

// ===== 5. 提案 =====
C.push(h1("5. 町への提案"));
C.push(bullet("ダイヤ検討：終端・折返し付近の余裕（パディング）区間を可視化できた。時刻表を実態に近い所要時間へ見直す基礎資料として活用できる。"));
C.push(bullet("折返し処理：正解データ生成側の到着検出は本実証で是正した。実運用システム側の折返し判定にも同じ考え方を適用すれば、さらに堅牢化できる。"));
C.push(bullet("GPS弱点への対応：丘陵地の弱点地点は、複数台展開時のアンテナ配置・測位方式の検討材料とする。"));
C.push(bullet("スケール：書込は無料枠内で約4台まで対応可能。台数拡大時の主コストはSIM通信費であり、費用の見通しが立てやすい。"));
C.push(p("以上より、本実証は「低コストで実用水準のバスロケーションが1台構成でも実現可能」であることを示した。次年度以降の発注仕様策定・複数台展開の判断材料として活用されたい。",{bold:true}));

const doc=new Document({
  styles:{
    default:{document:{run:{font:FONT,size:22}}},
    paragraphStyles:[
      {id:"Heading1",name:"Heading 1",basedOn:"Normal",next:"Normal",quickFormat:true,
        run:{size:30,bold:true,font:FONT,color:"1F3864"},paragraph:{spacing:{before:240,after:160},outlineLevel:0}},
      {id:"Heading2",name:"Heading 2",basedOn:"Normal",next:"Normal",quickFormat:true,
        run:{size:25,bold:true,font:FONT,color:"2E5496"},paragraph:{spacing:{before:180,after:100},outlineLevel:1}},
    ]
  },
  numbering:{config:[{reference:"b",levels:[{level:0,format:LevelFormat.BULLET,text:"•",alignment:AlignmentType.LEFT,
    style:{paragraph:{indent:{left:600,hanging:300}}}}]}]},
  sections:[{
    properties:{page:{size:{width:12240,height:15840},margin:{top:1440,right:1440,bottom:1440,left:1440}}},
    footers:{default:new Footer({children:[new Paragraph({alignment:AlignmentType.CENTER,
      children:[new TextRun({children:[PageNumber.CURRENT],size:18})]})]})},
    children:C
  }]
});
// 引数で出力先を指定できる。既定ではreports内に保存する。
const outputPath = process.argv[2] || path.join(__dirname, "瑞穂町バスロケPoC報告書_v5.docx");
Packer.toBuffer(doc).then(buf=>{fs.writeFileSync(outputPath,buf);console.log("written v5  bytes="+buf.length);});
