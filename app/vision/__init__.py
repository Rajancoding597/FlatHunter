"""Provider-neutral vision extraction for FlatHunter listing ingestion.

Import concrete providers or the factory from their modules. Keeping package
initialization side-effect free lets schema and ingestion unit tests run without
loading application credentials.
"""
