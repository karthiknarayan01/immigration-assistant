"""Sample queries to test the agent after setup."""

TEST_QUERIES = [
    "What is the H1B specialty occupation requirement and which job titles get the most RFEs?",
    "My H1B is at a consulting company placing me at a client site. What are the risks?",
    "What are current H1B processing times for premium processing?",
    "Can I do CPT from day one of my F1 program?",
    "My F1 OPT is expiring and my H1B was selected in the lottery. What happens?",
    "I got a 214b denial. What should I say differently at my next F1 interview?",
    "I am a software engineer visiting the US on B1 for a conference. What should I NOT say at the port of entry?",
    "What is the difference between L1A and L1B and which is harder to get approved?",
    "What are the EB1A extraordinary ability criteria and how many do I need to meet?",
    "What is EB2 NIW and who is a good candidate for it?",
    "I am from India. What is the realistic wait time for EB2 green card right now?",
]

if __name__ == "__main__":
    print("Try these queries after running: adk run immigration_agent\n")
    for i, q in enumerate(TEST_QUERIES, 1):
        print(f"{i}. {q}")
