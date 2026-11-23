/**
 * Spanish for API text that carries numbers or team names (forecast labels, week labels...). Used by `translate`
 * when a phrase has no exact entry in es.json. Each rule: an English regex and a function building the Spanish.
 */
import type { Dict } from "./index";

// Set at the start of each (synchronous) translatePattern call, read by the rules below.
let ES: Dict = {};

const NOUN: Record<string, string> = {
  points: "puntos", assists: "asistencias", rebounds: "rebotes", "total points": "puntos totales",
  "passing yards": "yardas por pase", "passing TDs": "TD de pase", "rushing yards": "yardas por tierra",
  receptions: "recepciones", "receiving yards": "yardas por recepción", "rushing/receiving TDs": "TD por tierra o recepción",
  goals: "goles", "total goals": "goles totales", pts: "pts", reb: "reb", ast: "ast",
};
const noun = (n: string) => NOUN[n] ?? ES[n] ?? n;
const side = (s: string) => ({ home: "el local", away: "el visitante", over: "más de", under: "menos de" } as Record<string, string>)[s] ?? s;
const NOUNS = Object.keys(NOUN).sort((a, b) => b.length - a.length).map((n) => n.replace(/[/+]/g, "\\$&")).join("|");

// Injury details come as "<body part> <type>" ("Knee Surgery", "Hamstring Not Specified").
const BODY: Record<string, string> = { Knee: "rodilla", Hamstring: "isquiotibiales", Ankle: "tobillo", Achilles: "tendón de Aquiles", Hip: "cadera",
  Shoulder: "hombro", Heel: "talón", Groin: "ingle", Foot: "pie", Calf: "pantorrilla", Back: "espalda", Leg: "pierna", "Lower Leg": "parte baja de la pierna",
  Abdomen: "abdomen", Toe: "dedo del pie", Quadriceps: "cuádriceps", Oblique: "oblicuo", Hand: "mano", Wrist: "muñeca", Elbow: "codo", Finger: "dedo",
  Thumb: "pulgar", Neck: "cuello", Chest: "pecho", Ribs: "costillas", Thigh: "muslo", Face: "cara", Eye: "ojo", Head: "cabeza", Pectoral: "pectoral",
  Forearm: "antebrazo", Shin: "espinilla", Pelvis: "pelvis", Glute: "glúteo", Biceps: "bíceps", Triceps: "tríceps", Arm: "brazo", Nose: "nariz" };
const KIND: Record<string, string> = { Surgery: "cirugía", Strain: "distensión", Sprain: "esguince", Soreness: "molestias", Bruise: "contusión",
  "Not Specified": "sin especificar", Inflammation: "inflamación", Fracture: "fractura", "Plantar Fasciitis": "fascitis plantar", "Pinched Nerve": "nervio pinzado",
  Spasms: "espasmos", Tendinitis: "tendinitis", Tendinopathy: "tendinopatía", Dislocation: "luxación", Contusion: "contusión", Tear: "rotura", Injury: "lesión",
  Rupture: "rotura", Stress: "estrés", Impingement: "pinzamiento", Laceration: "laceración", Bursitis: "bursitis" };
const SOLO: Record<string, string> = { Illness: "Enfermedad", Rest: "Descanso", Concussion: "Conmoción cerebral", Personal: "Motivos personales",
  "Not Injury Related": "No relacionado con lesión", Suspension: "Suspensión", "G League": "Liga G", Conditioning: "Preparación física", Coach: "Decisión técnica" };
const BODY_KEYS = Object.keys(BODY).sort((a, b) => b.length - a.length);   // longest first ("Lower Leg" before "Leg")
export function injury(d: string): string | null {
  if (SOLO[d]) return SOLO[d];
  const part = BODY_KEYS.find((b) => d === b || d.startsWith(b + " "));
  if (!part) return null;
  const rest = d.slice(part.length).trim();
  const cap = (x: string) => x[0].toUpperCase() + x.slice(1);
  if (!rest) return cap(BODY[part]);
  return KIND[rest] ? `${cap(KIND[rest])} de ${BODY[part]}` : null;
}
const inj = (d: string) => ES[d] ?? injury(d) ?? d;

type Rule = [RegExp, (...m: string[]) => string];

const RULES: Rule[] = [
  // Winner / moneyline
  [/^(.+) wins \(including overtime\)$/, (_, s) => `gana ${side(s)} (incluida la prórroga)`],
  [/^(.+) wins \(a tie is a push\)$/, (_, s) => `gana ${side(s)} (un empate se anula)`],
  [/^(.+) to win$/, (_, s) => `gana ${side(s)}`],
  // Spread: "home -4.5 covers", "BOS +3 covers (exact = push)"
  [/^(\S+) ([+-]?[\d.]+) covers( \(exact = push\))?$/, (_, s, n, push) => `${side(s)} ${n} cubre${push ? " (exacto = se anula)" : ""}`],
  // Over / under lines: "over 8.5 points (i.e. 9+)", "under 231.5 total points", "over 20 points (exactly 20 = push)"
  [new RegExp(`^(over|under|home|away) ([\\d.]+) (${NOUNS})(?: \\(i\\.e\\. (\\d+)\\+\\))?(?: \\(exactly ([\\d.]+) = push\\))?$`),
    (_, s, n, nn, ie, ex) => `${side(s)} ${n} ${noun(nn)}${ie ? ` (o sea, ${ie}+)` : ""}${ex ? ` (exactamente ${ex} = se anula)` : ""}`],
  // Thresholds: "25+ points", "2+ rushing/receiving TDs"
  [new RegExp(`^([\\d.]+)\\+ (${NOUNS})$`), (_, n, nn) => `${n}+ ${noun(nn)}`],
  // Soccer totals: "Over 2.5 goals (3+)"
  [/^Over ([\d.]+) goals \((\d+)\+\)$/, (_, n, k) => `Más de ${n} goles (${k}+)`],
  // Player props on the board: "Over 8.5 pts"
  [new RegExp(`^(Over|Under) ([\\d.]+) (${NOUNS})$`), (_, s, n, nn) => `${s === "Over" ? "Más de" : "Menos de"} ${n} ${noun(nn)}`],
  // NBA player-prop explanations (backend ml/player_model.py explain_player)
  [/^Projected around (\d+) minutes \(recent average (\d+) over last games played\)$/, (_, a, b) => `Proyectado para unos ${a} minutos (promedio reciente de ${b} en los últimos partidos jugados)`],
  [/^Rate of ([\d.]+) (\w+) per 36 minutes this season \(last 10 games: ([\d.]+)\)$/, (_, a, st, b) => `Ritmo de ${a} ${noun(st)} por 36 minutos esta temporada (últimos 10 partidos: ${b})`],
  [/^Opponent allows (\d+)% (more|fewer) points per possession than league average$/, (_, n, m) => `El rival permite un ${n}% ${m === "more" ? "más" : "menos"} de puntos por posesión que el promedio de la liga`],
  [/^Expected game pace (\d+)% (faster|slower) than league average$/, (_, n, m) => `Ritmo de juego esperado un ${n}% ${m === "faster" ? "más rápido" : "más lento"} que el promedio de la liga`],
  // NFL player-prop explanations (nfl/ml/player_model.py)
  [/^Played (\d+)% of offensive snaps over the last 3 games$/, (_, n) => `Jugó el ${n}% de las jugadas ofensivas en los últimos 3 partidos`],
  [/^Averaging ([\d.]+) pass attempts over the last 3 games$/, (_, n) => `Promedia ${n} intentos de pase en los últimos 3 partidos`],
  [/^Averaging ([\d.]+) carries over the last 3 games \((\d+)% of team carries\)$/, (_, n, p) => `Promedia ${n} acarreos en los últimos 3 partidos (${p}% de los acarreos del equipo)`],
  [/^Averaging ([\d.]+) targets over the last 3 games \((\d+)% target share\)$/, (_, n, p) => `Promedia ${n} objetivos en los últimos 3 partidos (${p}% de los objetivos del equipo)`],
  [/^([\d.]+) carries \+ targets per game recently$/, (_, n) => `${n} acarreos + objetivos por partido recientemente`],
  [new RegExp(`^Season average: ([\\d.]+) (${NOUNS}) per game$`), (_, n, nn) => `Promedio de la temporada: ${n} ${noun(nn)} por partido`],
  [/^Opponent allows (\d+)% (more|fewer) (rushing|passing) yards per game than league average$/, (_, n, m, k) => `El rival permite un ${n}% ${m === "more" ? "más" : "menos"} de yardas ${k === "rushing" ? "por tierra" : "por pase"} por partido que el promedio de la liga`],
  // Availability reasons (ml/player_features.py, nfl/ml/features.py)
  [/^inactive in each of the team's last 2 games \((.+)\)$/, (_, r) => `inactivo en los últimos 2 partidos del equipo (${ES[r] ?? r})`],
  [/^inactive last game \((.+)\)$/, (_, r) => `inactivo en el último partido (${ES[r] ?? r})`],
  [/^did not play last game \((.+)\)$/, (_, r) => `no jugó el último partido (${ES[r] ?? r})`],
  [/^(Out|Doubtful|Questionable) on the injury report \((.+)\)$/, (_, st, d) => `${ES[st] ?? st} en el informe de lesiones (${inj(d)})`],
  [/^roster status (\w+)$/, (_, c) => `estado en la plantilla: ${c}`],
  [/^No forecast: (.+)$/, (_, r) => `Sin pronóstico: ${ES[r] ?? matchRules(r) ?? r}`],
  [/^Projected (home|away) points$/, (_, sd) => `Puntos proyectados del ${sd === "home" ? "local" : "visitante"}`],
  [/^Expected (home|away) goals$/, (_, sd) => `Goles esperados del ${sd === "home" ? "local" : "visitante"}`],
  [/^Expected (\w+(?: \w+)?)$/, (_, st) => { const w = noun(st); return `${w[0].toUpperCase()}${w.slice(1)} ${/^(yardas|asistencias|recepciones)/.test(w) ? "esperadas" : "esperados"}`; }],
  [/^point-in-time backtest forecasts blended (\d+)% model \/ (\d+)% opening no-vig price$/, (_, a, b) => `pronósticos de backtest de su momento, combinados ${a}% modelo / ${b}% precio de apertura sin margen`],
  // Calendar / season labels
  [/^Week (\d+)$/, (_, n) => `Semana ${n}`],
  [/^(\d{4}(?:-\d{2})?) season$/, (_, s) => `Temporada ${s}`],
  [/^(\d{4}(?:-\d{2})?) regular season$/, (_, s) => `Temporada regular ${s}`],
  [/^Matchday (\d+)$/, (_, n) => `Jornada ${n}`],
  [/^Round (\d+)$/, (_, n) => `Jornada ${n}`],
  [/^Game (\d+)$/, (_, n) => `Partido ${n}`],
  [/^Group ([A-Z0-9]+)$/, (_, g) => `Grupo ${g}`],
  [/^(\S+) leads series (\d+)-(\d+)$/, (_, t, a, b) => `${t} gana la serie ${a}-${b}`],
  [/^Series tied (\d+)-(\d+)$/, (_, a, b) => `Serie empatada ${a}-${b}`],
  [/^(\S+) wins series (\d+)-(\d+)$/, (_, t, a, b) => `${t} ganó la serie ${a}-${b}`],
  // Injury report reasons: "Out (Knee) on the injury report"
  [/^(.+?) \((.+)\) on the injury report$/, (_, st, d) => `${ES[st] ?? st} (${inj(d)}) en el informe de lesiones`],
  [/^(.+) on the injury report$/, (_, st) => `${ES[st] ?? st} en el informe de lesiones`],
];

// Results are pure functions of (dictionary, text): remember them (bounded) so repeated labels skip the regex scan.
const MEMO = new WeakMap<Dict, Map<string, string | null>>();
const MEMO_MAX = 5000;

export function translatePattern(s: string, dict: Dict): string | null {
  let memo = MEMO.get(dict);
  if (!memo) MEMO.set(dict, (memo = new Map()));
  const hit = memo.get(s);
  if (hit !== undefined) return hit;
  ES = dict;
  const out = matchRules(s);
  if (memo.size >= MEMO_MAX) memo.clear();
  memo.set(s, out);
  return out;
}

function matchRules(s: string): string | null {
  const i = injury(s);
  if (i) return i;
  for (const [re, fn] of RULES) {
    const m = s.match(re);
    if (m) return fn(...m);
  }
  return null;
}
