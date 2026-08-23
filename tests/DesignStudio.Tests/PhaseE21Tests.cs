using DesignStudio.Model;
using DesignStudio.Model.Advisor;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// E21 - SI/PI-aware design advisor.
// Exit criterion: given a board that fails a DDR5 lane or a PDN target, the
// advisor recommends the right SI/PI fix (back-drill / decap / EQ / length /
// reference / stackup) - not just a part change. These pin that behaviour down
// deterministically, plus the advice-contract round-trip and context scaling.
// ============================================================================

public class SiPiAdvisorTests
{
    private static bool Has(AdviceReport r, AdviceOp op) => r.Actions.Any(a => a.Op == op);
    private static string AllReasons(AdviceReport r) => string.Join(" | ", r.Actions.Select(a => a.Reason));

    [Fact]
    public void ClosedEyeWithViaStubRecommendsBackdrill()
    {
        var s = new CircuitState();
        s.SignalIntegrity.Add(new CsChannel
        {
            Net = "PCIE_TX0", DataRateGbps = 32, InsertionLossDb = -7.5,
            EyeHeightMv = 0, EyeWidthUi = 0.0, MaskPass = false, HasViaStub = true
        });
        var r = SiPiAdvisor.Recommend(s);

        Assert.True(Has(r, AdviceOp.Backdrill));
        Assert.Contains("PCIE_TX0", AllReasons(r));
        Assert.False(Has(r, AdviceOp.SetEq));      // modest loss + a stub => the stub is the fix
    }

    [Fact]
    public void LossLimitedEyeRecommendsEqualisation()
    {
        var s = new CircuitState();
        s.SignalIntegrity.Add(new CsChannel
        {
            Net = "SFP_LANE", DataRateGbps = 56, InsertionLossDb = -16.0,
            EyeHeightMv = 4, EyeWidthUi = 0.05, MaskPass = false, HasViaStub = false
        });
        var r = SiPiAdvisor.Recommend(s);

        Assert.True(Has(r, AdviceOp.SetEq));
        Assert.False(Has(r, AdviceOp.Backdrill));  // no stub to drill
    }

    [Fact]
    public void PdnOverTargetAddsDecapAtTheAntinode()
    {
        var s = new CircuitState();
        s.PowerIntegrity.Add(new CsPdnRail
        {
            Net = "VDD_CORE", TargetMohm = 5, WorstZMohm = 18, WorstFreqMhz = 80,
            OverTarget = true, PlacementHint = "place near U1 at the 80 MHz antinode"
        });
        var r = SiPiAdvisor.Recommend(s);

        var add = r.Actions.FirstOrDefault(a => a.Op == AdviceOp.AddDecap);
        Assert.NotNull(add);
        Assert.Equal("VDD_CORE", add!.Net);
        Assert.Contains("antinode", add.Reason);
    }

    [Fact]
    public void DecapWorseningAntiResonanceIsMoved()
    {
        var s = new CircuitState();
        s.PowerIntegrity.Add(new CsPdnRail
        {
            Net = "VDD_CORE", TargetMohm = 5, WorstZMohm = 22, WorstFreqMhz = 120, OverTarget = true,
            DecapRanking = new List<string> { "C7: +5.1 mOhm", "C12: -3.4 mOhm" }
        });
        var r = SiPiAdvisor.Recommend(s);

        var move = r.Actions.FirstOrDefault(a => a.Op == AdviceOp.MoveDecap);
        Assert.NotNull(move);
        Assert.Equal("C12", move!.ForRef);        // the negative-contribution decap
    }

    [Fact]
    public void DdrHoldViolationRecommendsLengthTune()
    {
        var s = new CircuitState();
        s.DdrLanes.Add(new CsDdrLane
        {
            Lane = "DQ0", DataRateMtps = 6400, WorstSetupPs = 18, WorstHoldPs = -9,
            WorstBit = "DQ3", Pass = false
        });
        var r = SiPiAdvisor.Recommend(s);

        Assert.True(Has(r, AdviceOp.LengthTune));
        Assert.False(Has(r, AdviceOp.SetEq));      // setup margin is positive
        Assert.Contains("DQ3", AllReasons(r));
    }

    [Fact]
    public void DdrSetupViolationRecommendsEqualisation()
    {
        var s = new CircuitState();
        s.DdrLanes.Add(new CsDdrLane
        {
            Lane = "DQ1", DataRateMtps = 6400, WorstSetupPs = -11, WorstHoldPs = 25,
            WorstBit = "DQ9", Pass = false
        });
        var r = SiPiAdvisor.Recommend(s);

        Assert.True(Has(r, AdviceOp.SetEq));
        Assert.False(Has(r, AdviceOp.LengthTune)); // hold margin is positive
    }

    [Fact]
    public void PassingBoardProducesNoSiPiActions()
    {
        var s = new CircuitState();
        s.SignalIntegrity.Add(new CsChannel
        { Net = "OK", DataRateGbps = 16, InsertionLossDb = -4, EyeHeightMv = 220, EyeWidthUi = 0.6, MaskPass = true });
        s.PowerIntegrity.Add(new CsPdnRail { Net = "VDD", TargetMohm = 10, WorstZMohm = 4, OverTarget = false });
        s.DdrLanes.Add(new CsDdrLane { Lane = "DQ0", WorstSetupPs = 30, WorstHoldPs = 28, Pass = true });

        var r = SiPiAdvisor.Recommend(s);
        Assert.Empty(r.Actions);
        Assert.Contains("No SI/PI sign-off failures", r.Summary);
    }
}

public class AdviceContractTests
{
    [Fact]
    public void RoundTripsTheDeterministicReport()
    {
        var s = new CircuitState();
        s.DdrLanes.Add(new CsDdrLane
        { Lane = "DQ0", WorstSetupPs = 5, WorstHoldPs = -9, WorstBit = "DQ3", Pass = false });
        var report = SiPiAdvisor.Recommend(s);

        string json = AdviceContract.Serialize(report);
        var back = AdviceContract.Parse(json);

        Assert.Equal(report.Actions.Count, back.Actions.Count);
        Assert.Equal(report.Actions[0].Op, back.Actions[0].Op);
        Assert.Equal(report.Actions[0].Net, back.Actions[0].Net);
        Assert.Equal(report.Actions[0].ForRef, back.Actions[0].ForRef);
    }

    [Fact]
    public void ParsesAModelResponse()
    {
        string json = """
            {
              "schema": "design-studio.advice/1",
              "summary": "one fix",
              "actions": [
                { "op": "backdrill", "net": "PCIE_TX0", "reason": "kill the stub" }
              ],
              "risks": ["adds a fab step"]
            }
            """;
        var r = AdviceContract.Parse(json);
        Assert.Single(r.Actions);
        Assert.Equal(AdviceOp.Backdrill, r.Actions[0].Op);
        Assert.Equal("PCIE_TX0", r.Actions[0].Net);
        Assert.Single(r.Risks);
    }

    [Fact]
    public void RejectsMalformedAndWrongSchema()
    {
        Assert.Throws<AdviceFormatException>(() => AdviceContract.Parse("{not json"));
        Assert.Throws<AdviceFormatException>(() => AdviceContract.Parse("{\"schema\":\"something/else\"}"));
        Assert.Throws<AdviceFormatException>(() => AdviceContract.Parse(
            "{\"schema\":\"design-studio.advice/1\",\"actions\":[{\"op\":\"frobnicate\"}]}"));
    }
}

public class ContextScopeTests
{
    [Fact]
    public void KeepsFailingNetsAndCapsTheHealthyRemainder()
    {
        var s = new CircuitState();
        // 60 healthy routed nets + 2 failing channels
        for (int i = 0; i < 60; i++)
            s.Nets.Add(new CsNet { Name = $"NET{i}", FullyRouted = true, Members = { "U1.1", "U2.1" } });
        s.Nets.Add(new CsNet { Name = "BADLANE", FullyRouted = true, Members = { "U1.9" } });
        s.Nets.Add(new CsNet { Name = "BADRAIL", FullyRouted = true, Members = { "U1.10" } });
        s.SignalIntegrity.Add(new CsChannel { Net = "BADLANE", MaskPass = false, EyeHeightMv = 0 });
        s.PowerIntegrity.Add(new CsPdnRail { Net = "BADRAIL", OverTarget = true });

        ContextScope.Apply(s, new ContextBudget(MaxNets: 10, MaxComponents: 10));

        Assert.True(s.Nets.Count <= 10);
        Assert.Contains(s.Nets, n => n.Name == "BADLANE");   // failing nets always kept
        Assert.Contains(s.Nets, n => n.Name == "BADRAIL");
        Assert.False(string.IsNullOrEmpty(s.ContextNote));
        Assert.Contains("omitted", s.ContextNote!);
    }

    [Fact]
    public void SmallBoardIsLeftUnchanged()
    {
        var s = new CircuitState();
        for (int i = 0; i < 5; i++) s.Nets.Add(new CsNet { Name = $"N{i}" });
        ContextScope.Apply(s, new ContextBudget(MaxNets: 200, MaxComponents: 200));
        Assert.Equal(5, s.Nets.Count);
        Assert.Null(s.ContextNote);
    }
}
