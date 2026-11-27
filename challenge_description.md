# 1. Challenge Overview

## Challenge Title

Identification of relevant Federal Court judgements and preparation of a legal summary

## Sector / Domain

Insurance

## Summary (2 sentences)

Instead of manually searching through hundreds of Federal Court judgements, lawyers can access the precedents most relevant to their case in a very short space of time. An AI-powered summary extracts the key legal arguments and presents them in a convincing and clear manner for use in legal proceedings.

## Context and Background

Why should highly qualified lawyers spend their time identifying court judgements, reading the text of the judgement and compiling a one-page summary from it, when they could instead be applying their expertise to the legal assessment and implementation of the findings?

This is precisely where Agentic AI’s potential lies.

Federal Court judgements form the basis for consistent and legally sound case handling. At the same time, analysing them is time-consuming, repetitive and often highly standardised: understanding the facts, identifying legal issues, following the court’s reasoning, extracting key statements and assessing their relevance to practice. These steps largely follow a recurring pattern and are therefore ideally suited to being supported by Agentic AI.

Why you should pick this challenge?

The search for relevant Federal Court judgements in the field of accident insurance is currently largely carried out manually and requires a significant amount of research by legal specialists. In addition, the judgements found must be analysed, categorised and converted into summaries that are both comprehensible and legally sound, which demands a great deal of expert knowledge and time. As a result, routine legal tasks tie up valuable resources that could otherwise be used for the actual legal assessment and advice. This creates a twofold benefit for Suva: on the one hand, it increases the efficiency of legal work; on the other, valuable knowledge derived from case law is made available more quickly and to a wider audience. The quality of legal expertise is maintained or even improved, as specialists have more time to devote to the actual legal analysis and to advising the organisation. This will improve a valuable process at a major Swiss institution, and you can play a part in this process improvement.

## Problem Description

In the first step, relevant Federal Supreme Court judgements are identified from all available publications and prioritised according to their legal relevance in relation to the specific case. The judgements are then summarised in legally precise summaries in German and French, whilst consistently adhering to specific technical terminology and Suva’s established internal style. Finally, all summaries are stored in a central, searchable and query-enabled knowledge base, so that they can be used efficiently for future research and case handling.

1. Navigate to bger.ch and search for available publications from the last 3 months.
2. Identify relevant publications (key words, i.e. Suva or Accident + use context from provided summaries) and generate a relevance score and explanations why you selected / or not.
3. Summarize highly relevant Federal Court judgements according to style, structure and language in the provided summary examples. Generate both a German and a French version of the summary (not a simple translation) taking specific vocabulary into account.
4. Build a clear UI to communicate original court judgement documents, relevance score (sortable), explanation why relevant/not (triage), summary in both languages, possibility to summarize additional court judgements.
5. Bonus: improve and extend the 4 steps to provide a better customer experience.

## Main Objective

Preparation of a legally and stylistically accurate summary of selected relevant Federal Court judgements and presentation in UI

# 2. Resources, Tools & Support

## Datasets (type, format, access)

Source: www.bger.ch supplemented by a collection of summaries compiled by humans in German and French.

## Supporting Materials

Collection of summaries compiled by humans in German and French (possible input on top).

# 3. Technical Preferences

## Recommended Platforms

The solution must be deployable in a sovereign setting (air-gapped, on-premises or Swiss cloud).

## Specific AI Models

Apertus 1.5

## Model Sources

Apertus v1.5 (8B or 70B) from Hugging Face (huggingface.co/swiss-ai), accessed via the CSCS / Keera Gateway.

Check the specific model requirements here:
https://hackapertus.notion.site/zurich-challenges-model-requirements

# 4. Outputs & Evaluation

## Expected Outputs

1. Summaries in a presentable UI (legal experts as customers), code for the whole project and documentation of your work.
2. Git repo (URL)
3. Technical Report (pdf)
4. Demo video (URL)
5. Pitch deck (pdf)


## Success Criteria

The summary should be comparable to the human-generated summary in terms of style and legal precision. Furthermore, all judgements that have been summarised by a human should also be correctly identified by the prototype. Any additional summaries must be correctly identified in a justifiable manner.

Challenge ranking:

1. Suva's legal specialists qualify your top 10 highly relevant summaries as accurate according to the provided examples and to a hold-out set of summaries not provided to the hacking team.
2. Your highly scored relevant court judgements are really relevant in the view of Suva's legal experts (you didn't miss anything relevant).

## Evaluation Criteria

1. Suva's legal specialists qualify your 10 highly relevant summaries as accurate according to the provided examples (provides a clear quality score).
2. Your 30 high scored relevant court judgements are really relevant in the view of Suva's legal experts (provides a clear hit score).
3. Bonus: Project/Code is easy to transfer to Azure.
