namespace DesignStudio.Model;

/// <summary>
/// Connectivity engine: computes the ratsnest (shortest unrouted connections)
/// per net using union-find over pads, vias and trace endpoints, then a greedy
/// MST between disconnected islands. Display-level connectivity is 2D (the
/// exact layer-aware answer comes from the C++ engine's netIslandCount).
/// </summary>
public static class Ratsnest
{
    public record Line(double Ax, double Ay, double Bx, double By, int NetId);

    private const double TolMm = 0.15;   // endpoint-to-pad / endpoint-to-endpoint snap tolerance

    private sealed class UnionFind
    {
        private readonly int[] parent;
        public UnionFind(int n) { parent = new int[n]; for (int i = 0; i < n; i++) parent[i] = i; }
        public int Find(int a) { while (parent[a] != a) { parent[a] = parent[parent[a]]; a = parent[a]; } return a; }
        public void Union(int a, int b) { int ra = Find(a), rb = Find(b); if (ra != rb) parent[ra] = rb; }
    }

    public static List<Line> Compute(BoardDocument doc)
    {
        var lines = new List<Line>();
        foreach (var net in doc.Nets)
            ComputeNet(doc, net.Id, lines);
        return lines;
    }

    public static int UnroutedCount(BoardDocument doc) => Compute(doc).Count;

    private static void ComputeNet(BoardDocument doc, int netId, List<Line> outLines)
    {
        // Nodes: pads of this net (world coords), vias, then trace endpoints.
        var padPos = new List<(double x, double y)>();
        foreach (var fp in doc.Footprints)
            foreach (var pad in fp.Pads)
                if (pad.NetId == netId)
                    padPos.Add(fp.PadWorld(pad));

        if (padPos.Count < 2) return;   // nothing to connect

        var traces = doc.Traces.Where(t => t.NetId == netId && !t.IsPour).ToList();
        var vias = doc.Vias.Where(v => v.NetId == netId).ToList();
        int nPads = padPos.Count;
        int nVias = vias.Count;
        int n = nPads + nVias + traces.Count * 2;
        var pos = new (double x, double y)[n];
        for (int i = 0; i < nPads; i++) pos[i] = padPos[i];
        for (int i = 0; i < nVias; i++) pos[nPads + i] = (vias[i].X, vias[i].Y);
        int eBase = nPads + nVias;
        for (int i = 0; i < traces.Count; i++)
        {
            pos[eBase + 2 * i] = (traces[i].Ax, traces[i].Ay);
            pos[eBase + 2 * i + 1] = (traces[i].Bx, traces[i].By);
        }

        var uf = new UnionFind(n);
        // each trace connects its own endpoints
        for (int i = 0; i < traces.Count; i++) uf.Union(eBase + 2 * i, eBase + 2 * i + 1);
        // snap all node pairs within tolerance (pads/vias to endpoints, endpoints to endpoints)
        for (int i = 0; i < n; i++)
            for (int j = i + 1; j < n; j++)
                if (Dist2(pos[i], pos[j]) <= TolMm * TolMm)
                    uf.Union(i, j);
        // endpoints landing inside a pad rectangle also connect
        // (cheap approximation: within half pad diagonal of the centre)
        int padIdx = 0;
        foreach (var fp in doc.Footprints)
            foreach (var pad in fp.Pads)
            {
                if (pad.NetId != netId) continue;
                double reach = Math.Max(pad.W, pad.H) / 2 + 0.05;
                for (int e = nPads; e < n; e++)
                    if (Dist2(pos[padIdx], pos[e]) <= reach * reach)
                        uf.Union(padIdx, e);
                padIdx++;
            }
        // endpoints landing on a via connect through it
        for (int v = 0; v < nVias; v++)
        {
            double reach = vias[v].DiameterMm / 2 + 0.05;
            for (int e = eBase; e < n; e++)
                if (Dist2(pos[nPads + v], pos[e]) <= reach * reach)
                    uf.Union(nPads + v, e);
        }

        // Greedy MST between islands, measured pad-to-pad (those are the visible anchors).
        while (true)
        {
            double best = double.MaxValue;
            int bi = -1, bj = -1;
            for (int i = 0; i < nPads; i++)
                for (int j = i + 1; j < nPads; j++)
                {
                    if (uf.Find(i) == uf.Find(j)) continue;
                    double d = Dist2(padPos[i], padPos[j]);
                    if (d < best) { best = d; bi = i; bj = j; }
                }
            if (bi < 0) break;   // single island — net fully connected
            outLines.Add(new Line(padPos[bi].x, padPos[bi].y, padPos[bj].x, padPos[bj].y, netId));
            uf.Union(bi, bj);
        }
    }

    private static double Dist2((double x, double y) a, (double x, double y) b)
    {
        double dx = a.x - b.x, dy = a.y - b.y;
        return dx * dx + dy * dy;
    }
}
