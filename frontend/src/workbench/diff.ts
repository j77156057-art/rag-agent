// 极简行级 diff（LCS），用于「AI 改写」替换前的统一差异预览。
// 不引第三方依赖：选区代码通常几十行，O(n*m) 足够。
// 注意：行号与文本严格按切分后的行一一对应，不做 trim/判空删行，
// 保证 diff 行结构稳定（空行也是行）。

export type DiffKind = 'same' | 'del' | 'add'

export interface DiffRow {
  kind: DiffKind
  /** 原文（旧）行号，从 1 起；add 行为 null */
  oldNo: number | null
  /** 新文行号，从 1 起；del 行为 null */
  newNo: number | null
  text: string
}

export interface DiffStats {
  adds: number
  dels: number
  same: number
  /** 两侧文本归一化后完全相同 */
  unchanged: boolean
}

/** 按行切分，保留结尾换行语义："a\n" -> ["a",""]（末行空串渲染为空行）。 */
function toLines(s: string): string[] {
  return s.replace(/\r\n/g, '\n').split('\n')
}

type Op = DiffKind // LCS 回溯操作：same / del（旧有新无）/ add（新有旧无）

export function diffLines(oldText: string, newText: string): { rows: DiffRow[]; stats: DiffStats } {
  const a = toLines(oldText)
  const b = toLines(newText)
  const n = a.length
  const m = b.length

  // dp[i][j] = a[i:] 与 b[j:] 的 LCS 长度
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }

  // 回溯成操作序列（顺序）
  const ops: Array<{ op: Op; text: string }> = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ op: 'same', text: a[i] })
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      ops.push({ op: 'del', text: a[i] })
      i++
    } else {
      ops.push({ op: 'add', text: b[j] })
      j++
    }
  }
  while (i < n) ops.push({ op: 'del', text: a[i++] })
  while (j < m) ops.push({ op: 'add', text: b[j++] })

  // 绑定两侧行号；del/add 相邻时按统一 diff 顺序排列（先删后加），不做额外对齐
  const rows: DiffRow[] = []
  let oldNo = 0
  let newNo = 0
  for (const r of ops) {
    if (r.op === 'same') {
      oldNo++
      newNo++
      rows.push({ kind: 'same', oldNo, newNo, text: r.text })
    } else if (r.op === 'del') {
      oldNo++
      rows.push({ kind: 'del', oldNo, newNo: null, text: r.text })
    } else {
      newNo++
      rows.push({ kind: 'add', oldNo: null, newNo, text: r.text })
    }
  }

  let adds = 0
  let dels = 0
  let same = 0
  for (const r of rows) {
    if (r.kind === 'add') adds++
    else if (r.kind === 'del') dels++
    else same++
  }
  return { rows, stats: { adds, dels, same, unchanged: adds === 0 && dels === 0 } }
}
