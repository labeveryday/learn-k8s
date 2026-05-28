#!/usr/bin/env python3
"""
Carbon Code Image Generation Example

Demonstrates using the generate_code_image tool to create beautiful
code screenshots using Carbon and Playwright.

Prerequisites:
- Dependencies: pip install playwright && playwright install chromium

Includes hub integration for session tracking and metrics.
"""

import sys
import argparse
import time
from pathlib import Path

# Add repo root to path for imports
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

from strands import Agent
from src.tools.carbon_image import (
    generate_code_image,
    generate_code_image_from_file,
    list_carbon_themes,
)
from src.models import anthropic_model
from src.config import CARBON_IMAGE_PROMPT
from src.hub import (
    create_session_manager,
    MetricsExporter,
    AgentRegistry,
)
from src.hub.session import generate_run_id

# Agent configuration
AGENT_ID = "carbon-code-imager"
AGENT_NAME = "Carbon Code Image Generator"

# Sample code snippets for demonstration
SAMPLE_CODE = {
    "python": '''def fibonacci(n: int) -> list[int]:
    """Generate Fibonacci sequence."""
    if n <= 0:
        return []
    elif n == 1:
        return [0]

    fib = [0, 1]
    for _ in range(2, n):
        fib.append(fib[-1] + fib[-2])
    return fib

print(fibonacci(10))''',

    "javascript": '''const debounce = (fn, delay) => {
  let timeoutId;
  return (...args) => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => fn(...args), delay);
  };
};

// Usage
const handleSearch = debounce((query) => {
  console.log(`Searching: ${query}`);
}, 300);''',

    "rust": '''use std::collections::HashMap;

fn word_count(text: &str) -> HashMap<String, usize> {
    let mut counts = HashMap::new();

    for word in text.split_whitespace() {
        let word = word.to_lowercase();
        *counts.entry(word).or_insert(0) += 1;
    }

    counts
}

fn main() {
    let text = "hello world hello rust";
    println!("{:?}", word_count(text));
}''',

    "go": '''package main

import (
    "fmt"
    "sync"
)

func worker(id int, jobs <-chan int, results chan<- int, wg *sync.WaitGroup) {
    defer wg.Done()
    for job := range jobs {
        fmt.Printf("Worker %d processing job %d\\n", id, job)
        results <- job * 2
    }
}

func main() {
    jobs := make(chan int, 100)
    results := make(chan int, 100)
    var wg sync.WaitGroup

    for w := 1; w <= 3; w++ {
        wg.Add(1)
        go worker(w, jobs, results, &wg)
    }

    for j := 1; j <= 5; j++ {
        jobs <- j
    }
    close(jobs)

    wg.Wait()
    close(results)
}''',
}


def main():
    parser = argparse.ArgumentParser(
        description="Generate beautiful code screenshots with Carbon"
    )
    parser.add_argument(
        "--code",
        type=str,
        default=None,
        help="Code to render (or use --sample)"
    )
    parser.add_argument(
        "--sample",
        type=str,
        choices=list(SAMPLE_CODE.keys()),
        default="python",
        help="Use a sample code snippet"
    )
    parser.add_argument(
        "--language",
        type=str,
        default="auto",
        help="Programming language for syntax highlighting"
    )
    parser.add_argument(
        "--theme",
        type=str,
        default="seti",
        help="Carbon theme (run with --list-themes to see options)"
    )
    parser.add_argument(
        "--background",
        type=str,
        default="rgba(171,184,195,1)",
        help="Background color in rgba format"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Output directory for generated images"
    )
    parser.add_argument(
        "--list-themes",
        action="store_true",
        help="List all available themes and exit"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Use interactive agent mode"
    )
    parser.add_argument(
        "--no-hub",
        action="store_true",
        help="Disable hub tracking"
    )

    args = parser.parse_args()

    # List themes and exit
    if args.list_themes:
        themes = list_carbon_themes()
        print("\nAvailable Carbon Themes:")
        print("-" * 40)
        for theme in themes["themes"]:
            print(f"  {theme}")
        print("\nRecommended by category:")
        for category, theme_list in themes["recommended"].items():
            print(f"  {category}: {', '.join(theme_list)}")
        print(f"\nDefault: {themes['default']}")
        return

    # Ensure output directory exists
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    # Initialize hub components (unless disabled)
    run_id = None
    registry = None
    metrics = None

    if not args.no_hub:
        run_id = generate_run_id(AGENT_ID)

        # Register agent
        registry = AgentRegistry()
        registry.register(
            agent_id=AGENT_ID,
            description="Generate beautiful code screenshots using Carbon and AgentCore Browser",
            tags=["carbon", "code", "screenshot", "agentcore-browser"],
        )

        # Initialize metrics
        metrics = MetricsExporter(
            agent_id=AGENT_ID,
            run_id=run_id,
        )

        print(f"Run ID: {run_id}")

    start_time = time.time()
    success = False

    if args.interactive:
        # Interactive agent mode
        print(f"\n{AGENT_NAME}")
        print("Available tools: generate_code_image, generate_code_image_from_file, list_carbon_themes")
        print("Type 'exit' to quit\n")

        # Create session manager for interactive mode
        session_manager = None
        if not args.no_hub:
            session_manager = create_session_manager(agent_id=AGENT_ID, run_id=run_id)

        model = anthropic_model()
        agent = Agent(
            model=model,
            tools=[generate_code_image, generate_code_image_from_file, list_carbon_themes],
            session_manager=session_manager,
            system_prompt=CARBON_IMAGE_PROMPT.format(output_dir=args.output_dir),
        )

        generation_count = 0
        while True:
            try:
                user_input = input("\nYou: ").strip()
                if user_input.lower() in ["exit", "quit"]:
                    print("Goodbye!")
                    success = True
                    break
                if not user_input:
                    continue

                response = agent(user_input)
                print(f"\nAgent: {response}")
                generation_count += 1

            except KeyboardInterrupt:
                print("\nGoodbye!")
                success = True
                break

        if metrics:
            metrics.set_stats("generation_count", generation_count)

    else:
        # Direct generation mode
        code = args.code if args.code else SAMPLE_CODE.get(args.sample, SAMPLE_CODE["python"])
        language = args.language if args.language != "auto" else args.sample

        print(f"Generating code image...")
        print(f"Language: {language}")
        print(f"Theme: {args.theme}")
        print(f"\nCode:\n{code[:100]}{'...' if len(code) > 100 else ''}\n")

        result = generate_code_image(
            code=code,
            language=language,
            theme=args.theme,
            background_color=args.background,
            output_dir=args.output_dir,
        )

        if result["success"]:
            print(f"Success! Image saved to: {result['file_path']}")
            success = True
            if metrics:
                metrics.set_stats("operation", "generate")
                metrics.set_stats("output_file", result["file_path"])
                metrics.set_stats("theme", args.theme)
                metrics.set_stats("language", language)
        else:
            print(f"Error: {result.get('error', 'Unknown error')}")
            if metrics:
                metrics.set_stats("error", result.get("error"))

    # Export metrics and record run
    if metrics:
        elapsed = time.time() - start_time
        metrics.set_timing("total_duration", elapsed)
        metrics_path = metrics.export()
        print(f"Metrics saved to: {metrics_path}")

    if registry:
        registry.record_run(agent_id=AGENT_ID, run_id=run_id, success=success)


if __name__ == "__main__":
    main()
