# DBaba for VS Code

Open this folder in VS Code and press F5 using an Extension Development Host launch configuration (type `extensionHost`, argument `--extensionDevelopmentPath=${workspaceFolder}`). Run `npm test` here with Node.js 22 or later. No npm dependencies are required.

Set `dbaba.url` (default `http://127.0.0.1:8765`) and `dbaba.database` in Settings. Use **DBaba: Set API Key** to save your key privately, or **DBaba: Remove API Key** to delete it. Remote connections require `dbaba.allowRemote` and HTTPS. No requests are made until you check SQL or submit a question.

Place the cursor in SQL and run **DBaba: Check this query**, or run **DBaba: Check all queries in this file**. Use the lightbulb to apply a safe correction, preview a change statement, or copy a correction for dynamic SQL. The question mark placeholders represent your application variables. The extension never executes SQL itself.

Open the DBaba activity bar icon to ask a question. Each request starts a fresh chat.

SQL discovery is lexical, not a full host-language parser. Concatenated strings and complex nested expressions may not be recognized. Corrections that could break string syntax are previewed instead of applied. Review all corrections before using them.
