"""Evaluation benchmark experiment runner for ContextMesh."""

import asyncio
import json
import os
import sys
from typing import Any, Dict, List
from agent.graph import ContextMeshAgent
from evals.evaluators.metrics import (
    calculate_tool_selection_accuracy,
    calculate_citation_precision,
    calculate_permission_compliance,
    calculate_groundedness
)


async def run_benchmark(dataset_path: str) -> Dict[str, Any]:
    with open(dataset_path, "r", encoding="utf-8") as f:
        benchmarks = json.load(f)

    agent = ContextMeshAgent()
    results = []

    print("=" * 70)
    print(f"🚀 Running ContextMesh Evaluation Benchmark ({len(benchmarks)} questions)")
    print("=" * 70)

    for item in benchmarks:
        q_id = item["id"]
        category = item["category"]
        question = item["question"]
        expected_tools = item.get("expected_tools", [])
        requires_approval = item.get("requires_approval", False)
        required_facts = item.get("required_facts", [])

        print(f"\n[Test {q_id}] ({category}) Q: {question}")
        resp = await agent.run(query=question, thread_id=f"bench_{q_id}")

        tool_acc = calculate_tool_selection_accuracy(resp.plan, expected_tools) if resp.plan else 0.0
        cit_prec = calculate_citation_precision(resp)
        perm_comp = calculate_permission_compliance(resp, requires_approval)
        groundedness = calculate_groundedness(resp, required_facts)

        score = (tool_acc + cit_prec + perm_comp + groundedness) / 4.0

        res_record = {
            "id": q_id,
            "category": category,
            "tool_selection_accuracy": round(tool_acc, 2),
            "citation_precision": round(cit_prec, 2),
            "permission_compliance": round(perm_comp, 2),
            "groundedness": round(groundedness, 2),
            "composite_score": round(score, 2),
            "requires_approval_triggered": resp.requires_approval
        }
        results.append(res_record)
        print(f"  -> Score: {score:.2f} | ToolAcc: {tool_acc:.2f} | Grounded: {groundedness:.2f} | ApprovalGate: {resp.requires_approval}")

    # Aggregated stats
    avg_tool_acc = sum(r["tool_selection_accuracy"] for r in results) / len(results)
    avg_citation = sum(r["citation_precision"] for r in results) / len(results)
    avg_perm = sum(r["permission_compliance"] for r in results) / len(results)
    avg_groundedness = sum(r["groundedness"] for r in results) / len(results)
    avg_composite = sum(r["composite_score"] for r in results) / len(results)

    summary = {
        "total_queries": len(results),
        "mean_composite_score": round(avg_composite, 3),
        "mean_tool_selection_accuracy": round(avg_tool_acc, 3),
        "mean_citation_precision": round(avg_citation, 3),
        "mean_permission_compliance": round(avg_perm, 3),
        "mean_groundedness": round(avg_groundedness, 3),
        "results": results
    }

    print("\n" + "=" * 70)
    print("📊 BENCHMARK SUMMARY RESULTS")
    print("=" * 70)
    print(f"Overall Composite Score: {summary['mean_composite_score'] * 100:.1f}%")
    print(f"Tool Selection Accuracy: {summary['mean_tool_selection_accuracy'] * 100:.1f}%")
    print(f"Groundedness:            {summary['mean_groundedness'] * 100:.1f}%")
    print(f"Citation Precision:      {summary['mean_citation_precision'] * 100:.1f}%")
    print(f"Permission Compliance:   {summary['mean_permission_compliance'] * 100:.1f}%")
    print("=" * 70)

    return summary


if __name__ == "__main__":
    default_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "datasets", "benchmark_questions.json")
    path = sys.argv[1] if len(sys.argv) > 1 else default_path
    asyncio.run(run_benchmark(path))
