import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from summarization_claim_audit.audit import audit, claims, evidence
from summarization_claim_audit.cli import main
from summarization_claim_audit.phrases import SUMMARIZATION, NOSUMMARIZATION

GOOD = 'from transformers import pipeline\npipeline("summarization")'


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, path, text):
        dest = self.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        return dest

    def cli(self, *flags):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = main([str(self.root), *flags])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_good(self):
        self.put('README.md', 'summarizes text')
        self.put('app.py', GOOD)
        self.assertEqual(self.cli(), (0, 'PASS\n'))

    def test_bad_high(self):
        self.put('README.md', 'summarizes text')
        self.put('app.py', 'pass')
        code, text = self.cli()
        self.assertEqual(code, 2)
        self.assertIn('HIGH documented-summarization-claim-false', text)

    def test_test_only(self):
        self.put('README.md', 'summarizes text')
        self.put('tests/worker.py', GOOD)
        self.assertEqual(self.cli()[0], 0)
        self.assertIn('MEDIUM', self.cli()[1])
        self.assertEqual(self.cli('--fail-on', 'MEDIUM')[0], 2)

    def test_negative(self):
        self.put('README.md', 'does not support summarization')
        self.put('app.py', GOOD)
        self.assertEqual(self.cli()[0], 2)
        result = audit(self.root)
        self.assertEqual(len(result['findings']), 1)
        self.assertEqual(result['findings'][0]['id'], 'documented-no-summarization-claim-false')
        self.assertEqual(result['findings'][0]['severity'], 'HIGH')

    def test_no_source_asymmetry(self):
        self.assertEqual(self.cli()[0], 0)
        self.put('README.md', 'no summarization')
        self.assertEqual(self.cli()[0], 0)
        self.put('README.md', 'summarizes text')
        self.assertEqual(self.cli()[0], 2)

    def test_json_verbose_ignore(self):
        self.put('README.md', 'summarizes text')
        self.put('app.py', GOOD)
        code, output = self.cli('--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['status'], 'PASS')
        self.assertIn('production_evidence', self.cli('--verbose')[1])
        self.assertEqual(self.cli('--ignore', 'app.py')[0], 2)
        self.assertEqual(self.cli('--ignore', 'README.md')[0], 0)

    def test_version(self):
        self.assertEqual(self.cli('--version'), (0, '0.1.0\n'))

    def test_errors(self):
        self.assertEqual(self.cli('--unknown')[0], 1)
        self.assertEqual(self.cli('--fail-on', 'LOW')[0], 1)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.root/'absent')]), 1)

    def test_docs_selection(self):
        self.put('other.md', 'summarizes text')
        self.assertEqual(self.cli()[0], 0)
        self.put('nested/docs/deep/file.md', 'summarizes text')
        self.assertEqual(self.cli()[0], 2)

    def test_excluded_sources(self):
        self.put('README.md', 'summarizes text')
        for name in ['fixtures/a.py', 'docs/a.py', 'examples/a.py']:
            self.put(name, GOOD)
        self.assertEqual(audit(self.root)['production_evidence'], [])
        self.assertEqual(audit(self.root)['test_evidence'], [])
        self.assertEqual(self.cli()[0], 2)

    def test_test_names(self):
        self.put('README.md', 'summarizes text')
        for name in ['test_a.py', 'a_test.py', 'tests/nested/a.py']:
            self.put(name, GOOD)
        self.assertEqual(len(audit(self.root)['test_evidence']), 3)
        self.assertEqual(self.cli()[0], 0)

    def test_symlinks(self):
        outside = self.put('fixtures/a.py', GOOD)
        (self.root/'linked.py').symlink_to(outside)
        (self.root/'linked_dir').symlink_to(outside.parent, target_is_directory=True)
        self.assertEqual(audit(self.root)['production_evidence'], [])

    def test_one_per_family(self):
        self.put('README.md', 'summarizes text\n\nsummarizes reports')
        self.put('docs/a.md', 'summarizes text')
        self.assertEqual(len(audit(self.root)['findings']), 1)

    def test_self_audit(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(audit(root)['status'], 'PASS')
        self.assertEqual(claims((root/'README.md').read_text()), ([], []))

    def test_module_entry(self):
        result = subprocess.run([sys.executable, '-m', 'summarization_claim_audit', str(self.root)], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, 'PASS\n'))

    def test_console_declaration(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn('summarization-claim-audit = "summarization_claim_audit.cli:main"', (root/'pyproject.toml').read_text())

    def test_fences_inline_normalization(self):
        self.assertEqual(claims('```text\nsummarizes text\n```\n\n   ~~~~\nno summarization\n   ~~~~'), ([], []))
        self.assertEqual(claims('`SUMMARIZES-TEXT`'), (['summarizes text'], []))
        self.assertEqual(claims("doesn't support summarization"), ([], ['does not support summarization']))
        self.assertEqual(claims('summarizes\n text'), (['summarizes text'], []))
        self.assertEqual(claims('summarizes\n\ntext'), ([], []))
        self.assertEqual(claims('resummarizes text'), ([], []))

    def test_negative_span_removal(self):
        self.assertEqual(claims('no automatic text summarization')[0], ['automatic text summarization'])
        self.assertEqual(claims('does not summarize text'), ([], ['does not summarize text']))


YES = {
'pipeline_direct': 'import transformers\ntransformers.pipeline("summarization")',
'pipeline_alias': 'import transformers as tf\ntf.pipeline(task="SUMMARIZATION_en_to_de")',
'pipeline_from': 'from transformers import pipeline as p\np("summarization")',
'pipeline_nested': 'from transformers.pipelines import pipeline\npipeline("en-summarization-de")',
'pipeline_construct': 'from transformers import SummarizationPipeline as SP\nSP()',
'pipeline_attribute': 'import transformers as t\nt.SummarizationPipeline()',
'gensim_from': 'from gensim.summarization import summarize as s\ns("text")',
'gensim_module': 'from gensim import summarization\nsummarization.summarize("text")',
'gensim_alias': 'import gensim.summarization as g\ng.summarize("text")',
'gensim_full': 'import gensim.summarization\ngensim.summarization.summarize("text")',
'sumy_bound': 'from sumy.summarizers.lex_rank import LexRankSummarizer as L\ns = L()\ns(doc, 3)',
'sumy_self': 'import sumy.summarizers.lsa as m\nclass C:\n def f(self):\n  self.s = m.LsaSummarizer()\n  return self.s(doc, 3)',
'sumy_chained': 'from sumy.summarizers.text_rank import TextRankSummarizer\nTextRankSummarizer()(doc, 3)',
'sumy_alias': 'from sumy.summarizers.luhn import LuhnSummarizer\ns = LuhnSummarizer()\nt = s\nt(doc, 3)',
}
NO = {
 'truncation': 'summary = text[:100]\nsummary = sentences[:3]',
 'keyword': 'from rake_nltk import Rake\nr = Rake()\nr.extract_keywords_from_text(text)',
 'tfidf': 'from sklearn.feature_extraction.text import TfidfVectorizer\nTfidfVectorizer().fit_transform(text)',
 'sentence': 'from nltk import sent_tokenize\nsent_tokenize(text)',
 'topic': 'from gensim.models import LdaModel\nLdaModel(corpus)',
 'headline': 'from transformers import pipeline\npipeline("headline-generation")',
 'sentiment': 'from transformers import pipeline\npipeline("sentiment-analysis")',
 'translation': 'from transformers import pipeline\npipeline("translation_en_to_de")',
 'seq2seq': 'from transformers import BartForConditionalGeneration, T5ForConditionalGeneration, PegasusForConditionalGeneration\nBartForConditionalGeneration()\nT5ForConditionalGeneration()\nPegasusForConditionalGeneration()',
 'bare': 'from transformers import pipeline, SummarizationPipeline\nfrom gensim.summarization import summarize\nfrom sumy.summarizers.lsa import LsaSummarizer',
 'strings': 'x = "pipeline(\'summarization\')"',
 'subprocess': 'import subprocess\nsubprocess.run(["summarize", "text"])',
 'uncalled': 'from sumy.summarizers.lsa import LsaSummarizer\ns = LsaSummarizer()',
 'unbound': 'summarizer(doc, 3)\nsummarize(text)\nobj.summarize(text)',
 'wrong_provider': 'from other import pipeline, SummarizationPipeline, summarize\npipeline("summarization")\nSummarizationPipeline()\nsummarize(text)',
 'shadow_function': 'from transformers import pipeline\ndef pipeline(task): pass\npipeline("summarization")',
 'shadow_class': 'from transformers import SummarizationPipeline\nclass SummarizationPipeline: pass\nSummarizationPipeline()',
 'shadow_module': 'import transformers\nclass transformers: pass\ntransformers.pipeline("summarization")',
 'shadow_sumy': 'from sumy.summarizers.lsa import LsaSummarizer\nclass LsaSummarizer: pass\nLsaSummarizer()(doc, 3)',
 'shadow_gensim': 'from gensim.summarization import summarize\ndef summarize(text): pass\nsummarize(text)',
 'resummarization': 'from transformers import pipeline\npipeline("resummarization")',
 'plural_task': 'from transformers import pipeline\npipeline("summarizations")',
 'variable_task': 'from transformers import pipeline\ntask = "summarization"\npipeline(task)',
 'invalid': 'from transformers import pipeline\npipeline("summarization"',
 'unbound_method': 'from sumy.summarizers.lsa import LsaSummarizer\nLsaSummarizer.__call__(doc, 3)',
}


def evidence_test(source, expected):
    def test(self):
        self.assertEqual(evidence(source), expected, source)
    return test


class EvidenceTests(unittest.TestCase):
    pass


for name, source in YES.items():
    setattr(EvidenceTests, 'test_yes_' + name, evidence_test(source, True))
for name, source in NO.items():
    setattr(EvidenceTests, 'test_no_' + name, evidence_test(source, False))
for name in ['EdmundsonSummarizer', 'RandomSummarizer', 'LexRankSummarizer', 'LsaSummarizer', 'TextRankSummarizer', 'LuhnSummarizer']:
    setattr(EvidenceTests, 'test_sumy_' + name, evidence_test('from sumy.summarizers.any import '+name+'\n'+name+'()(doc, 3)', True))


def phrase_test(phrase, negative):
    def test(self):
        yes, no = claims(phrase)
        self.assertIn(phrase, no if negative else yes)
        if negative:
            self.assertEqual(yes, [])
    return test


class PhraseTests(unittest.TestCase):
    pass


for i, phrase in enumerate(SUMMARIZATION):
    setattr(PhraseTests, 'test_positive_%02d' % i, phrase_test(phrase, False))
for i, phrase in enumerate(NOSUMMARIZATION):
    setattr(PhraseTests, 'test_negative_%02d' % i, phrase_test(phrase, True))
