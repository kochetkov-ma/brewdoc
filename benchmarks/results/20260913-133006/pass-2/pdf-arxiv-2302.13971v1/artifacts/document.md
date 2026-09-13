# arxiv-2302.13971v1.pdf

<!-- rendered by brewdoc from arxiv-2302.13971v1.pdf sha256 2e663675ae36ad12adb2f5a05281bac2747ecf8d23d92bedd9f937a89fee7136 -->

## Page 1

LLaMA: Open and Efficient Foundation Language Models

HugoTouvron?,ThibautLavril?, GautierIzacard?,XavierMartinet
Marie-AnneLachaux,TimotheeLacroix,BaptisteRoziere,NamanGoyal
EricHambro,FaisalAzhar,AurelienRodriguez,ArmandJoulin
EdouardGrave?,GuillaumeLample?
MetaAI

Abstract
WeintroduceLLaMA,acollectionoffounda-
tionlanguagemodelsrangingfrom7Bto65B
parameters. We train our models on trillions
oftokens, andshowthatitispossibletotrain
state-of-the-art models using publicly avail-
able datasets exclusively, without resorting
to proprietary and inaccessible datasets. In
particular, LLaMA-13B outperforms GPT-3
(175B) on most benchmarks, and LLaMA-
65B is competitive with the best models,
Chinchilla-70B and PaLM-540B. We release
allourmodelstotheresearchcommunity1.
1 Introduction
LargeLanguagesModels(LLMs)trainedonmas-
sivecorporaoftextshaveshowntheirabilitytoper-
formnewtasksfromtextualinstructionsorfroma
fewexamples(Brownetal.,2020). Thesefew-shot
propertiesfirstappearedwhenscalingmodelstoa
sufficientsize(Kaplanetal.,2020),resultingina
lineofworkthatfocusesonfurtherscalingthese
models(Chowdheryetal.,2022;Raeetal.,2021).
These efforts are based on the assumption that
more parameters will lead to better performance.
However,recentworkfromHoffmannetal.(2022)
shows that, for a given compute budget, the best
performancesarenotachievedbythelargestmod-
els,butbysmallermodelstrainedonmoredata.
The objective of the scaling laws from Hoff-
3202
beF
72
]LC.sc[
1v17931.2032:viXra

performance, a smaller one trained longer will
ultimately be cheaper at inference. For instance,
although Hoffmann et al. (2022) recommends
training a 10B model on 200B tokens, we find
that the performance of a 7B model continues to
improveevenafter1Ttokens.
The focus of this work is to train a series of
languagemodelsthatachievethebestpossibleper-
formanceatvariousinferencebudgets,bytraining
on more tokens than what is typically used. The
resultingmodels,calledLLaMA,rangesfrom7B
to65Bparameterswithcompetitiveperformance
comparedtothebestexistingLLMs. Forinstance,
LLaMA-13BoutperformsGPT-3onmostbench-
marks,despitebeing10xsmaller. Webelievethat
this model will help democratize the access and
studyofLLMs,sinceitcanberunonasingleGPU.
Atthehigher-endofthescale,our65B-parameter
model is also competitive with the best large lan-
guagemodelssuchasChinchillaorPaLM-540B.
Unlike Chinchilla, PaLM, or GPT-3, we only
usepubliclyavailabledata,makingourworkcom-
patible with open-sourcing, while most existing
models rely on data which is either not publicly
availableorundocumented(e.g. "Books-2TB"or
"Social media conversations"). There exist some
exceptions, notably OPT (Zhang et al., 2022),
GPT-NeoX (Black et al., 2022), BLOOM (Scao

## Page 2

2 Approach
Our training approach is similar to the methods
described in previous work (Brown et al., 2020;
Chowdhery et al., 2022), and is inspired by the
Chinchilla scaling laws (Hoffmann et al., 2022).
Wetrainlargetransformersonalargequantityof
textualdatausingastandardoptimizer.
2.1 Pre-trainingData
Ourtrainingdatasetisamixtureofseveralsources,
reportedinTable1,thatcoveradiversesetofdo-
mains. For the most part, we reuse data sources
thathavebeenleveragedtotrainotherLLMs,with
the restriction of only using data that is publicly
available,andcompatiblewithopensourcing. This
leadstothefollowingmixtureofdataandtheper-
centagetheyrepresentinthetrainingset:
EnglishCommonCrawl[67%]. Wepreprocess
five CommonCrawl dumps, ranging from 2017
to 2020, with the CCNet pipeline (Wenzek et al.,
2020). This process deduplicates the data at the
line level, performs language identification with
afastTextlinearclassifiertoremovenon-English
pages and filters low quality content with an n-
gram language model. In addition, we trained a
linear model to classify pages used as references
in Wikipedia v.s. randomly sampled pages, and
discardedpagesnotclassifiedasreferences.
C4[15%]. Duringexploratoryexperiments,we
observed that using diverse pre-processed Com-
monCrawldatasetsimprovesperformance. Wethus
includedthepubliclyavailableC4dataset(Raffel
etal.,2020)inourdata. ThepreprocessingofC4
alsocontainsdeduplicationandlanguageidentifi-
cation steps: the main difference with CCNet is
thequalityfiltering,whichmostlyreliesonheuris-
ticssuchaspresenceofpunctuationmarksorthe
numberofwordsandsentencesinawebpage.
Github [4.5%]. We use the public GitHub
dataset available on Google BigQuery. We only
keptprojectsthataredistributedundertheApache,
BSD and MIT licenses. Additionally, we filtered
lowqualityfileswithheuristicsbasedontheline
length or proportion of alphanumeric characters,
andremovedboilerplate,suchasheaders,withreg-
ularexpressions. Finally,wededuplicatetheresult-
ingdatasetatthefilelevel,withexactmatches.
Wikipedia [4.5%]. We add Wikipedia dumps
from the June-August 2022 period, covering 20

```
Dataset  Samplingprop. Epochs Disksize
CommonCrawl 67.0%   1.10  3.3TB
C4          15.0%   1.06  783GB
Github       4.5%   0.64  328GB
Wikipedia    4.5%   2.45  83GB
Books        4.5%   2.23  85GB
ArXiv        2.5%   1.06  92GB
StackExchange 2.0%  1.03  78GB
Table1:Pre-trainingdata.Datamixturesusedforpre-
training, for each subset we list the sampling propor-
tion, numberofepochsperformedonthesubsetwhen
trainingon1.4Ttokens,anddisksize. Thepre-training
runson1Ttokenshavethesamesamplingproportion.
```

languages, which use either the Latin or Cyrillic
scripts: bg, ca, cs, da, de, en, es, fr, hr, hu, it,
nl,pl,pt,ro,ru,sl,sr,sv,uk. Weprocessthe
data to remove hyperlinks, comments and other
formattingboilerplate.
Gutenberg and Books3 [4.5%]. We include
twobookcorporainourtrainingdataset: theGuten-
bergProject,whichcontainsbooksthatareinthe
public domain, and the Books3 section of TheP-
ile(Gaoetal.,2020),apubliclyavailabledataset
for training large language models. We perform
deduplication at the book level, removing books
withmorethan90%contentoverlap.
ArXiv [2.5%]. We process arXiv Latex files
to add scientific data to our dataset. Following
Lewkowyczetal.(2022),weremovedeverything
beforethefirstsection,aswellasthebibliography.
Wealsoremovedthecommentsfromthe.texfiles,
andinline-expandeddefinitionsandmacroswritten
byuserstoincreaseconsistencyacrosspapers.
Stack Exchange [2%]. We include a dump of
Stack Exchange, a website of high quality ques-
tions and answers that covers a diverse set of do-
mains,rangingfromcomputersciencetochemistry.
Wekeptthedatafromthe28largestwebsites,re-
moved the HTML tags from text and sorted the
answersbyscore(fromhighesttolowest).
Tokenizer. We tokenize the data with the byte-
pair encoding (BPE) algorithm (Sennrich et al.,
2015), using the implementation from Sentence-
Piece(KudoandRichardson,2018). Notably,we
splitallnumbersintoindividualdigits,andfallback
tobytestodecomposeunknownUTF-8characters.

## Page 3

```
params dimension nheads nlayers learningrate batchsize ntokens
 6.7B   4096    32    32     3.0e?4   4M     1.0T
13.0B   5120    40    40     3.0e?4   4M     1.0T
32.5B   6656    52    60     1.5e?4   4M     1.4T
65.2B   8192    64    80     1.5e?4   4M     1.4T
   Table2: Modelsizes,architectures,andoptimizationhyper-parameters.
```

Overall, our entire training dataset contains
2.2

|  |  |  |  |  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  | LLaM | A 7B |  |  |
|  |  |  |  |  |  |  | LLaM | A 13B |  |  |
|  |  |  |  |  |  |  | LLaM LLaM | A 33B A 65B |  |  |
|  |  |  |  |  |  |  |  |  |  |  |
|  |  |  |  |  |  |  |  |  |  |  |
|  |  |  |  |  |  |  |  |  |  |  |
|  |  |  |  |  |  |  |  |  |  |  |

ournetworkisbasedonthetransformerarchitec-
ture (Vaswani et al., 2017). We leverage various
improvements that were subsequently proposed,
andusedindifferentmodelssuchasPaLM.Here
arethemaindifferencewiththeoriginalarchitec-
ture,andwherewewerefoundtheinspirationfor
thischange(inbracket):
Pre-normalization [GPT3]. To improve the
training stability, we normalize the input of each
transformersub-layer,insteadofnormalizingthe
output. WeusetheRMSNormnormalizingfunc-
tion,introducedbyZhangandSennrich(2019).
SwiGLU activation function [PaLM]. We re-
placetheReLUnon-linearitybytheSwiGLUac-
tivationfunction,introducedbyShazeer(2020)to
improvetheperformance. Weuseadimensionof
24dinsteadof4dasinPaLM.
3
RotaryEmbeddings[GPTNeo]. Weremovethe
absolutepositionalembeddings,andinstead,add
rotarypositionalembeddings(RoPE),introduced
bySuetal.(2021),ateachlayerofthenetwork.
Thedetailsofthehyper-parametersforourdif-
ferentmodelsaregiveninTable2.
2.3 Optimizer
Our models are trained using the AdamW opti-
mizer(LoshchilovandHutter,2017),withthefol-
lowing hyper-parameters: ? = 0.9,? = 0.95.
1 2
Weuse acosine learningrate schedule, such that
thefinallearningrateisequalto10%ofthemaxi-
mallearningrate. Weuseaweightdecayof0.1and
gradient clipping of 1.0. We use 2,000 warmup

1.5 0 200 400 600 800 1000 1200 1400
Billion of tokens
Figure1: Traininglossovertraintokensforthe7B,
13B,33B,and65models. LLaMA-33BandLLaMA-
65Bweretrainedon1.4Ttokens. Thesmallermodels
were trained on 1.0T tokens. All models are trained
withabatchsizeof4Mtokens.

steps,andvarythelearningrateandbatchsizewith
thesizeofthemodel(seeTable2fordetails).
2.4 Efficientimplementation
Wemakeseveraloptimizationstoimprovethetrain-
ingspeedofourmodels. First,weuseanefficient
implementationofthecausalmulti-headattention
toreducememoryusageandruntime. Thisimple-
mentation, available in the xformers library,2 is
inspired by Rabe and Staats (2021) and uses the
backwardfromDaoetal.(2022). Thisisachieved
bynotstoringtheattentionweightsandnotcom-
putingthekey/queryscoresthataremaskeddueto
thecausalnatureofthelanguagemodelingtask.
To further improve training efficiency, we re-
duced the amount of activations that are recom-
puted during the backward pass with checkpoint-
ing. More precisely, we save the activations that
are expensive to compute, such as the outputs of
linearlayers. Thisisachievedbymanuallyimple-
mentingthebackwardfunctionforthetransformer
layers,insteadofrelyingonthePyTorchautograd.
Tofullybenefitfromthisoptimization,weneedto
2https://github.com/facebookresearch/xformers

## Page 4

```
             BoolQ PIQA SIQA HellaSwag WinoGrande ARC-e ARC-c OBQA
GPT-3   175B  60.5 81.0  -    78.9   70.2   68.8 51.4  57.6
Gopher  280B  79.3 81.8 50.6  79.2   70.1    -    -     -
Chinchilla 70B 83.7 81.8 51.3 80.8   74.9    -    -     -
PaLM     62B  84.8 80.5  -    79.7   77.0   75.2 52.5  50.4
PaLM-cont 62B 83.9 81.4  -    80.6   77.0    -    -     -
PaLM    540B  88.0 82.3  -    83.4   81.1   76.6 53.0  53.4
          7B  76.5 79.8 48.9  76.1   70.1   72.8 47.6  57.2
         13B  78.1 80.1 50.4  79.2   73.0   74.8 52.7  56.4
LLaMA
         33B  83.1 82.3 50.4  82.8   76.0   80.0 57.8  58.6
         65B  85.3 82.8 52.3  84.2   77.0   78.9 56.0  60.2
         Table3: Zero-shotperformanceonCommonSenseReasoningtasks.
```

reduce the memory usage of the model by using
model and sequence parallelism, as described by
Korthikantietal.(2022). Moreover,wealsoover-
lapthecomputationofactivationsandthecommu-
nicationbetweenGPUsoverthenetwork(dueto
all_reduceoperations)asmuchaspossible.
Whentraininga65B-parametermodel,ourcode
processes around 380 tokens/sec/GPU on 2048
A100GPUwith80GBofRAM.Thismeansthat
training over our dataset containing 1.4T tokens
takesapproximately21days.
3 Mainresults
Followingpreviouswork(Brownetal.,2020),we
considerzero-shotandfew-shottasks,andreport
resultsonatotalof20benchmarks:
- Zero-shot. Weprovideatextualdescription
of the task and a test example. The model
eitherprovidesananswerusingopen-ended
generation,orrankstheproposedanswers.
- Few-shot. Weprovideafewexamplesofthe
task (between 1 and 64) and a test example.
Themodeltakesthistextasinputandgener-
atestheanswerorranksdifferentoptions.
WecompareLLaMAwithotherfoundationmod-
els, namely the non-publicly available language
modelsGPT-3(Brownetal.,2020),Gopher(Rae
et al., 2021), Chinchilla (Hoffmann et al., 2022)
and PaLM (Chowdhery et al., 2022), as well as
theopen-sourcedOPTmodels(Zhangetal.,2022),
GPT-J(WangandKomatsuzaki,2021),andGPT-
Neo (Black et al., 2022). In Section 4, we also
briefly compare LLaMA with instruction-tuned
models such as OPT-IML (Iyer et al., 2022) and
Flan-PaLM(Chungetal.,2022).

We evaluate LLaMA on free-form generation
tasks and multiple choice tasks. In the multiple
choice tasks, the objective is to select the most
appropriate completion among a set of given op-
tions,basedonaprovidedcontext. Weselectthe
completion with the highest likelihood given the
provided context. We follow Gao et al. (2021)
andusethelikelihoodnormalizedbythenumber
ofcharactersinthecompletion,exceptforcertain
datasets(OpenBookQA,BoolQ),forwhichwefol-
low Brown et al. (2020), and select a completion
based on the likelihood normalized by the likeli-
hoodofthecompletiongiven"Answer:" ascontext:
P(completion|context)/P(completion|"Answer:").

```
           0-shot 1-shot 5-shot 64-shot
GPT-3  175B 14.6 23.0 -    29.9
Gopher 280B 10.1 -   24.5  28.2
Chinchilla 70B 16.6 - 31.5 35.5
        8B  8.4 10.6  -    14.6
PaLM   62B  18.1 26.5 -    27.6
       540B 21.2 29.3 -    39.6
        7B  16.8 18.7 22.0 26.1
       13B  20.1 23.4 28.1 31.9
LLaMA
       33B  24.9 28.3 32.9 36.0
       65B  23.8 31.0 35.0 39.9
Table4: NaturalQuestions. Exactmatchperformance.
```

3.1 CommonSenseReasoning
We consider eight standard common sense rea-
soning benchmarks: BoolQ (Clark et al., 2019),
PIQA(Bisketal.,2020),SIQA(Sapetal.,2019),

## Page 5

HellaSwag(Zellersetal.,2019),WinoGrande(Sak-
aguchietal.,2021),ARCeasyandchallenge(Clark
et al., 2018) and OpenBookQA (Mihaylov et al.,
2018). ThesedatasetsincludeClozeandWinograd
styletasks,aswellasmultiplechoicequestionan-
swering. We evaluate in the zero-shot setting as
doneinthelanguagemodelingcommunity.
In Table 3, we compare with existing models
ofvarioussizesandreportnumbersfromthecor-
responding papers. First, LLaMA-65B outper-
formsChinchilla-70Bonallreportedbenchmarks
butBoolQ.Similarly,thismodelsurpassesPaLM-
540BeverywherebutonBoolQandWinoGrande.
LLaMA-13B model also outperforms GPT-3 on
mostbenchmarksdespitebeing10xsmaller.
3.2 Closed-bookQuestionAnswering
We compare LLaMA to existing large language
models on two closed-book question answering
benchmarks: Natural Questions (Kwiatkowski
etal.,2019)andTriviaQA(Joshietal.,2017). For
both benchmarks, we report exact match perfor-
manceinaclosedbooksetting,i.e.,wherethemod-
els do not have access to documents that contain
evidence to answer the question. In Table 4, we
reportperformanceonNaturalQuestions,andinTa-
ble5,wereportonTriviaQA.Onbothbenchmarks,
LLaMA-65Bachievestate-of-the-artsperformance
in the zero-shot and few-shot settings. More im-
portantly,theLLaMA-13Bisalsocompetitiveon
thesebenchmarkswithGPT-3andChinchilla,de-
spite being 5-10x smaller. This model runs on a
singleV100GPUduringinference.

```
           0-shot 1-shot 5-shot 64-shot
Gopher 280B 43.5 -   57.0  57.2
Chinchilla 70B 55.4 - 64.1 64.6
        7B  50.0 53.4 56.3 57.6
       13B  56.6 60.5 63.1 64.0
LLaMA
       33B  65.1 67.9 69.9 70.4
       65B  68.2 71.6 72.6 73.0
Table 5: TriviaQA. Zero-shot and few-shot exact
matchperformanceonthefiltereddevset.
```

3.3 ReadingComprehension
WeevaluateourmodelsontheRACEreadingcom-
prehension benchmark (Lai et al., 2017). This
dataset was collected from English reading com-
prehension exams designed for middle and high

```
            RACE-middle RACE-high
 GPT-3  175B   58.4      45.5
         8B    57.9      42.3
 PaLM   62B    64.3      47.5
        540B   68.1      49.1
         7B    61.1      46.9
        13B    61.6      47.2
 LLaMA
        33B    64.1      48.3
        65B    67.9      51.6
Table 6: Reading Comprehension. Zero-shot accu-
racy.
```

schoolChinesestudents. Wefollowtheevaluation
setupfromBrownetal.(2020)andreportresults
inTable6. Onthesebenchmarks,LLaMA-65Bis
competitivewithPaLM-540B,and,LLaMA-13B
outperformsGPT-3byafewpercents.
3.4 Mathematicalreasoning
Weevaluateourmodelsontwomathematicalrea-
soning benchmarks: MATH (Hendrycks et al.,
2021) and GSM8k (Cobbe et al., 2021). MATH
isadatasetof12Kmiddleschoolandhighschool
mathematicsproblemswritteninLaTeX.GSM8k
is a set of middle school mathematical problems.
In Table 7, we compare with PaLM and Min-
erva(Lewkowyczetal.,2022). Minervaisaseries
of PaLM models finetuned on 38.5B tokens ex-
tracted from ArXiv and Math Web Pages, while
neitherPaLMorLLaMAarefinetunedonmathe-
maticaldata. ThenumbersforPaLMandMinerva
are taken from Lewkowycz et al. (2022), and we
compare with and without maj1@k. maj1@k de-
notesevaluationswherewegeneratek samplesfor
eachproblemandperformamajorityvoting(Wang
etal.,2022). OnGSM8k,weobservethatLLaMA-
65BoutperformsMinerva-62B,althoughithasnot
beenfine-tunedonmathematicaldata.
3.5 Codegeneration
We evaluate the ability of our models to write
code from a natural language description on two
benchmarks: HumanEval(Chenetal.,2021)and
MBPP (Austin et al., 2021). For both tasks, the
model receives a description of the program in a
few sentences, as well as a few input-output ex-
amples. InHumanEval,italsoreceivesafunction
signature, and the prompt is formatted as natural
code with the textual description and tests in a

## Page 6

```
         MATH +maj1@k GSM8k +maj1@k
        8B 1.5  -    4.1   -
 PaLM  62B 4.4  -    33.0  -
      540B 8.8  -    56.5  -
        8B 14.1 25.4 16.2 28.4
 Minerva 62B 27.6 43.4 52.4 68.5
      540B 33.6 50.3 68.5 78.5
        7B 2.9  6.9  11.0 18.1
       13B 3.9  8.8  17.8 29.3
 LLaMA
       33B 7.1 15.2  35.6 53.1
       65B 10.6 20.5 50.9 69.7
Table7: Modelperformanceonquantitativereason-
ing datasets. For majority voting, we use the same
setup as Minerva, with k = 256 samples for MATH
andk = 100forGSM8k(Minerva540Busesk = 64
forMATHandandk =40forGSM8k). LLaMA-65B
outperforms Minerva 62B on GSM8k, although it has
notbeenfine-tunedonmathematicaldata.
```

docstring. ThemodelneedstogenerateaPython
program that fits the description and satisfies the
test cases. In Table 8, we compare the pass@1
scoresofourmodelswithexistinglanguagemod-
els that have not been finetuned on code, namely
PaLMandLaMDA(Thoppilanetal.,2022). PaLM
andLLaMAweretrainedondatasetsthatcontain
asimilarnumberofcodetokens.
As show in Table 8, for a similar number
of parameters, LLaMA outperforms other gen-
eral models such as LaMDA and PaLM, which
are not trained or finetuned specifically for code.
LLaMA with 13B parameters and more outper-
forms LaMDA 137B on both HumanEval and
MBPP.LLaMA65BalsooutperformsPaLM62B,
evenwhenitistrainedlonger. Thepass@1results
reported in this table were obtained by sampling
withtemperature0.1. Thepass@100andpass@80
metrics were obtained with temperature 0.8. We
usethesamemethodasChenetal.(2021)toobtain
unbiasedestimatesofthepass@k.
Itispossibletoimprovetheperformanceoncode
byfinetuningoncode-specifictokens. Forinstance,
PaLM-Coder (Chowdhery et al., 2022) increases
the pass@1 score of PaLM on HumanEval from
26.2% for PaLM to 36%. Other models trained
specificallyforcodealsoperformbetterthangen-
eralmodelsonthesetasks(Chenetal.,2021;Ni-
jkampetal.,2022;Friedetal.,2022). Finetuning
oncodetokensisbeyondthescopeofthispaper.

```
         Params HumanEval MBPP
 pass@        @1  @100 @1  @80
 LaMDA    137B 14.0 47.3 14.8 62.4
 PaLM      8B 3.6? 18.7? 5.0? 35.7?
 PaLM      62B 15.9 46.3? 21.4 63.2?
 PaLM-cont 62B 23.7 -  31.2 -
 PaLM     540B 26.2 76.2 36.8 75.0
           7B 10.5 36.5 17.7 56.2
           13B 15.8 52.5 22.0 64.0
 LLaMA
           33B 21.7 70.7 30.2 73.4
           65B 23.7 79.3 37.7 76.8
Table 8: Model performance for code generation.
We report the pass@ score on HumanEval and MBPP.
HumanEval generations are done in zero-shot and
MBBP with 3-shot prompts similar to Austin et al.
(2021). Thevaluesmarkedwith?arereadfromfigures
inChowdheryetal.(2022).
```

3.6 MassiveMultitaskLanguage
Understanding
The massive multitask language understanding
benchmark,orMMLU,introducedbyHendrycks
etal.(2020)consistsofmultiplechoicequestions
covering various domains of knowledge, includ-
ing humanities, STEM and social sciences. We
evaluateourmodelsinthe5-shotsetting,usingthe
examplesprovidedbythebenchmark,andreport
resultsinTable9. Onthisbenchmark,weobserve
that the LLaMA-65B is behind both Chinchilla-
70BandPaLM-540Bbyafewpercentinaverage,
andacrossmostdomains. Apotentialexplanation
is that we have used a limited amount of books
andacademicpapersinourpre-trainingdata,i.e.,
ArXiv,GutenbergandBooks3,thatsumsuptoonly
177GB,whilethesemodelsweretrainedonupto
2TB of books. This large quantity of books used
byGopher,ChinchillaandPaLMmayalsoexplain
whyGopheroutperformsGPT-3onthisbenchmark,
whileitiscomparableonotherbenchmarks.
3.7 Evolutionofperformanceduringtraining
Duringtraining,wetrackedtheperformanceofour
modelsonafewquestionansweringandcommon
sense benchmarks, and report them in Figure 2.
On most benchmarks, the performance improves
steadily,andcorrelateswiththetrainingperplexity
of the model (see Figure 1). The exceptions are
SIQA and WinoGrande. Most notably, on SIQA,
we observe a lot of variance in performance,

## Page 7

```
             Humanities STEM SocialSciences Other Average
GPT-NeoX 20B    29.8   34.9    33.7    37.7  33.6
GPT-3    175B   40.8   36.7    50.4    48.8  43.9
Gopher   280B   56.2   47.4    71.9    66.1  60.0
Chinchilla 70B  63.6   54.9    79.3    73.9  67.5
          8B    25.6   23.8    24.1    27.8  25.4
PaLM     62B    59.5   41.9    62.7    55.8  53.7
         540B   77.0   55.6    81.0    69.6  69.3
          7B    34.0   30.5    38.3    38.1  35.1
         13B    45.0   35.8    53.8    53.3  46.9
LLaMA
         33B    55.8   46.0    66.7    63.4  57.8
         65B    61.8   51.7    72.9    67.4  63.4
Table9: MassiveMultitaskLanguageUnderstanding(MMLU).Five-shotaccuracy.
```

that may indicate that this benchmark is not
reliable. On WinoGrande, the performance does
not correlate as well with training perplexity:
the LLaMA-33B and LLaMA-65B have similar
performanceduringthetraining.
4 InstructionFinetuning
Inthissection,weshowthatbrieflyfinetuningon
instructions data rapidly leads to improvements
on MMLU. Although the non-finetuned version
ofLLaMA-65Bisalreadyabletofollowbasicin-
structions,weobservethataverysmallamountof
finetuning improves the performance on MMLU,
and further improves the ability of the model to
follow instructions. Since this is not the focus of
thispaper,weonlyconductedasingleexperiment
followingthesameprotocolasChungetal.(2022)
totrainaninstructmodel,LLaMA-I.

```
      OPT         30B 26.1
      GLM        120B 44.8
      PaLM        62B 55.1
      PaLM-cont   62B 62.8
      Chinchilla  70B 67.5
      LLaMA       65B 63.4
      OPT-IML-Max 30B 43.2
      Flan-T5-XXL 11B 55.1
      Flan-PaLM   62B 59.6
      Flan-PaLM-cont 62B 66.1
      LLaMA-I     65B 68.9
Table 10: Instruction finetuning - MMLU (5-shot).
Comparisonofmodelsofmoderatesizewithandwith-
outinstructionfinetuningonMMLU.
```

InTable10,wereporttheresultsofourinstruct
modelLLaMA-IonMMLUandcomparewithex-
isting instruction finetuned models of moderate
sizes,namely,OPT-IML(Iyeretal.,2022)andthe
Flan-PaLMseries(Chungetal.,2022). Allthere-
portednumbersarefromthecorrespondingpapers.
Despitethesimplicityoftheinstructionfinetuning
approach used here, we reach 68.9% on MMLU.
LLaMA-I(65B)outperformsonMMLUexisting
instructionfinetunedmodelsofmoderatesizes,but
are still far from the state-of-the-art, that is 77.4
forGPTcode-davinci-002onMMLU(numbers
taken from Iyer et al. (2022)). The details of the
performance on MMLU on the 57 tasks can be
foundinTable16oftheappendix.

5 Bias,ToxicityandMisinformation

Large language models have been showed to re-
produce and amplify biases that are existing in
thetrainingdata(Shengetal.,2019;Kuritaetal.,
2019), and to generate toxic or offensive con-
tent(Gehmanetal.,2020). Asourtrainingdataset
containsalargeproportionofdatafromtheWeb,
we believe that it is crucial to determine the po-
tential for our models to generate such content.
TounderstandthepotentialharmofLLaMA-65B,
weevaluateondifferentbenchmarksthatmeasure
toxiccontentproductionandstereotypesdetection.
Whilewehaveselectedsomeofthestandardbench-
marks that are used by the language model com-
munity to indicate some of the issues with these
models,theseevaluationsarenotsufficienttofully
understandtherisksassociatedwiththesemodels.

## Page 8

TriviaQA HellaSwag NaturalQuestions

|  | TriviaQA |  |  |
| --- | --- | --- | --- |
| 250 500 750 1000 1250 |  |  | 15 |
|  | SI | QA |  |

```
  400 250 500 750 1000 1250 1500 500 250 500 750 1000 1250 150065.00 250 500 750 1000 1250 1500
        Billion of tokens   Billion of tokens   Billion of tokens
Figure2: Evolutionofperformanceonquestionansweringandcommonsensereasoningduringtraining.
```

5.1 RealToxicityPrompts
Languagemodelscangeneratetoxiclanguage,e.g.,
insults,hatespeechorthreats. Thereisaverylarge
range of toxic content that a model can generate,
makingathoroughevaluationchallenging. Several
recentwork(Zhangetal.,2022;Hoffmannetal.,
2022) have considered the RealToxicityPrompts
benchmark(Gehmanetal.,2020)asanindicator
ofhowtoxicistheirmodel. RealToxicityPrompts
consistsofabout100kpromptsthatthemodelmust
complete; then a toxicity score is automatically
evaluatedbymakingarequesttoPerspectiveAPI3.
Wedonothavecontroloverthepipelineusedby
thethird-partyPerspectiveAPI,makingcomparison
withpreviousmodelsdifficult.
Foreachofthe100kprompts,wegreedilygen-
erate with our models, and measure their toxic-
ity score. The score per prompt ranges from 0
(non-toxic)to1(toxic). InTable11,wereportour
averagedscoreonbasicandrespectfulpromptcat-
egoriesofRealToxicityPrompts. Thesescoresare
"comparable" with what we observe in the litera-
ture (e.g., 0.087 for Chinchilla) but the method-
ologies differ between these work and ours (in
termsofsamplingstrategy,numberofpromptsand
time of API). We observe that toxicity increases
3https://perspectiveapi.com/

```
               Basic Respectful
            7B 0.106  0.081
           13B 0.104  0.095
    LLaMA
           33B 0.107  0.087
           65B 0.128  0.141
Table11: RealToxicityPrompts. Werunagreedyde-
coderonthe100kpromptsfromthisbenchmark. The
"respectful" versions are prompts starting with "Com-
pletethefollowingsentenceinapolite,respectful,and
unbiased manner:", and "Basic" is without it. Scores
were obtained using the PerplexityAPI, with higher
scoreindicatingmoretoxicgenerations.
```

withthesizeofthemodel,especiallyforRespect-
ful prompts. This was also observed in previous
work(Zhangetal.,2022),withthenotableexcep-
tionofHoffmannetal.(2022)wheretheydonot
see a difference between Chinchilla and Gopher,
despitedifferentsizes. Thiscouldbeexplainedby
the fact that the larger model, Gopher, has worse
performance than Chinchilla, suggesting that the
relationbetweentoxicityandmodelsizemayonly
applywithinamodelfamily.

## Page 9

```
               LLaMA  GPT3  OPT
Gender          70.6   62.6 65.7
Religion        79.0   73.3 68.6
Race/Color      57.0   64.7 68.6
Sexualorientation 81.0 76.2 78.6
Age             70.1   64.4 67.8
Nationality     64.2   61.6 62.9
Disability      66.7   76.7 76.7
Physicalappearance 77.8 74.6 76.2
Socioeconomicstatus 71.5 73.8 76.2
Average         66.6   67.2 69.5
Table 12: CrowS-Pairs. We compare the level of bi-
ases contained in LLaMA-65B with OPT-175B and
GPT3-175B.Higherscoreindicateshigherbias.
```

5.2 CrowS-Pairs
WeevaluatethebiasesinourmodelontheCrowS-
Pairs(Nangiaetal.,2020). Thisdatasetallowsto
measure biases in 9 categories: gender, religion,
race/color,sexualorientation,age,nationality,dis-
ability,physicalappearanceandsocioeconomicsta-
tus. Eachexampleiscomposedofastereotypeand
an anti-stereotype, we measure the model prefer-
ence for the stereotypical sentence using the per-
plexity of both sentences in a zero-shot setting.
Higherscoresthusindicatehigherbias. Wecom-
parewithGPT-3andOPT-175BinTable12.
LLaMA compares slightly favorably to both
models on average. Our model is particularly bi-
asedinthereligioncategory(+10%comparedto
OPT-175B), followed by age and gender. We ex-
pectthesebiasestocomefromCommonCrawlde-
spitemultiplefilteringsteps.
5.3 WinoGender
To further investigate the biases of our model on
the gender category, we look at the WinoGender
benchmark(Rudingeretal.,2018),aco-reference
resolutiondataset. WinoGenderismadeofWino-
gradschema,andbiasesareevaluatedbydetermin-
ingifamodelco-referenceresolutionperformance
isimpactedbythegenderofthepronoun.
More precisely, each sentence has three men-
tions: an "occupation", a "participant", and a
"pronoun" where the pronoun is co-referencing
either the occupation or participant. We prompt
the model to determine the co-reference relation
and measure if it does so correctly according to

the context of the sentence. The goal is to reveal
if societal biases associated with occupations
have been captured by the model. For example,
a sentence in the WinoGender dataset is "The
nurse notified the patient that his shift would be
ending in an hour.", which is followed by 'His'
refers to. Wethencomparetheperplexityofthe
continuationsthe nurseandthe patienttoper-
formco-referenceresolutionwiththemodel. We
evaluatetheperformancewhenusing3pronouns:
"her/her/she","his/him/he"and"their/them/some-
one" (the different choices corresponding to the
grammaticalfunctionofthepronoun.
In Table 13, we report the co-reference scores
for the three different pronouns contained in the
dataset. Weobservethatourmodelissignificantly
better at performing co-reference resolution for
the "their/them/someone" pronouns than for the
"her/her/she"and"his/him/he"pronouns. Asimi-
lar observation was made in previous work (Rae
et al., 2021; Hoffmann et al., 2022), and is likely
indicativeofgenderbias. Indeed,inthecaseofthe
"her/her/she"and"his/him/he"pronouns,themodel
isprobablyusingthemajoritygenderoftheoccu-
pationtoperformco-referenceresolution,instead
ofusingtheevidenceofthesentence.
To further investigate this hypothesis, we look
at the set of "gotcha" cases for the "her/her/she"
and "his/him/he" pronouns in the WinoGender
dataset. Theses cases correspond to sentences in
which the pronoun does not match the majority
gender of the occupation, and the occupation is
the correct answer. In Table 13, we observe that
ourmodel,LLaMA-65B,makesmoreerrorsonthe
gotcha examples, clearly showing that it capture
societal biases related to gender and occupation.
The drop of performance exists for "her/her/she"
and"his/him/he"pronouns,whichisindicativeof
biasesregardlessofgender.
5.4 TruthfulQA
TruthfulQA(Linetal.,2021)aimstomeasurethe
truthfulnessofamodel,i.e.,itsabilitytoidentify
when a claim is true. Lin et al. (2021) consider
thedefinitionof"true"inthesenseof"literaltruth
abouttherealworld",andnotclaimsthatareonly
true in the context of a belief system or tradition.
Thisbenchmarkcanevaluatetherisksofamodel
to generate misinformation or false claims. The
questionsarewrittenindiversestyle,cover38cat-
egoriesandaredesignedtobeadversarial.

## Page 10

```
               7B  13B 33B  65B
 All           66.0 64.7 69.0 77.5
 her/her/she   65.0 66.7 66.7 78.8
 his/him/he    60.8 62.5 62.1 72.1
 their/them/someone 72.1 65.0 78.3 81.7
 her/her/she(gotcha) 64.2 65.8 61.7 75.0
 his/him/he(gotcha) 55.0 55.8 55.8 63.3
Table 13: WinoGender. Co-reference resolution ac-
curacy for the LLaMA models, for different pronouns
("her/her/she"and"his/him/he"). Weobservethatour
modelsobtainbetterperformanceon"their/them/some-
one'pronounsthanon"her/her/she"and"his/him/he',
whichislikelyindicativeofbiases.
```

```
              Truthful Truthful*Inf
          1.3B 0.31    0.19
  GPT-3    6B  0.22    0.19
         175B  0.28    0.25
           7B  0.33    0.29
          13B  0.47    0.41
  LLaMA
          33B  0.52    0.48
          65B  0.57    0.53
Table14: TruthfulQA.Wereportthefractionoftruth-
fulandtruthful*informativeanswers,asscoredbyspe-
cially trained models via the OpenAI API. We follow
theQApromptstyleusedinOuyangetal.(2022),and
reporttheperformanceofGPT-3fromthesamepaper.
```

In Table 14, we report the performance of our
modelsonbothquestionstomeasuretruthfulmod-
elsandtheintersectionoftruthfulandinformative.
Compared to GPT-3, our model scores higher in
bothcategories,buttherateofcorrectanswersis
stilllow,showingthatourmodelislikelytohallu-
cinateincorrectanswers.
6 Carbonfootprint
Thetrainingofourmodelshaveconsumedamas-
sive quantity of energy, responsible for the emis-
sionofcarbondioxide. Wefollowtherecentliter-
atureonthesubjectandbreakdownboththetotal
energyconsumptionandtheresultingcarbonfoot-
printinTable15. WefollowaformulaforWuetal.
(2022) to estimate the Watt-hour, Wh, needed to
trainamodel, aswellasthe tonsofcarbonemis-
sions,tCO eq. FortheWh,weusetheformula:
2
Wh = GPU-hx(GPUpowerconsumption)xPUE,

wherewesetthePowerUsageEffectiveness(PUE)
at1.1. Theresultingcarbonemissiondependson
thelocationofthedatacenterusedtotrainthenet-
work. Forinstance,BLOOMusesagridthatemits
0.057 kg CO eq/KWh leading to 27 tCO eq and
2 2
OPTagridthatemits0.231kgCO eq/KWh,lead-
2
ingto82tCO eq. Inthisstudy,weareinterestedin
2
comparingthecostincarbonemissionoftraining
of these models if they were trained in the same
data center. Hence, we do not take the location
of data center in consideration, and use, instead,
theUSnationalaveragecarbonintensityfactorof
0.385kgCO eq/KWh. Thisleadstothefollowing
2
formulaforthetonsofcarbonemissions:
tCO eq = MWhx0.385.
2
WeapplythesameformulatoOPTandBLOOM
forfaircomparison. ForOPT,weassumetraining
required34dayson992A100-80B(seetheirlogs4).
Finally,weestimatethatweused2048A100-80GB
foraperiodofapproximately5monthstodevelop
ourmodels. Thismeansthatdevelopingthesemod-
elswouldhavecostaround2,638MWhunderour
assumptions,andatotalemissionof1,015tCO eq.
2
We hope that releasing these models will help to
reducefuturecarbonemissionsincethetrainingis
alreadydone,andsomeofthemodelsarerelatively
smallandcanberunonasingleGPU.
7 Relatedwork
Language models are probability distributions
over sequences of words, tokens or charac-
ters(Shannon,1948,1951). Thistask,oftenframed
asnexttokenprediction,haslongbeenconsidereda
coreprobleminnaturallanguageprocessing(Bahl
etal.,1983;Brownetal.,1990). BecauseTuring
(1950)proposedtomeasuremachineintelligence
by using language through the "imitation game",
languagemodelinghasbeenproposedasabench-
marktomeasureprogresstowardartificialintelli-
gence(Mahoney,1999).
Architecture. Traditionally, language models
were based on n-gram count statistics (Bahl
et al., 1983), and various smoothing techniques
were proposed to improve the estimation of rare
events(Katz,1987;KneserandNey,1995). Inthe
pasttwodecades,neuralnetworkshavebeensuc-
cessfully applied to the language modelling task,
4https://github.com/facebookresearch/metaseq/
tree/main/projects/OPT/chronicles

## Page 11

```
                       GPUPower          Totalpower Carbonemitted
               GPUType           GPU-hours
                       consumption       consumption (tCO eq)
                                                       2
   OPT-175B   A100-80GB  400W    809,472  356MWh      137
   BLOOM-175B A100-80GB  400W    1,082,880 475MWh     183
   LLaMA-7B   A100-80GB  400W     82,432  36MWh        14
   LLaMA-13B  A100-80GB  400W    135,168  59MWh        23
   LLaMA-33B  A100-80GB  400W    530,432  233MWh       90
   LLaMA-65B  A100-80GB  400W    1,022,362 449MWh     173
Table15: Carbonfootprintoftrainingdifferentmodelsinthesamedatacenter. WefollowWuetal.(2022)
to compute carbon emission of training OPT, BLOOM and our models in the same data center. For the power
consumption of a A100-80GB, we take the thermal design power for NVLink systems, that is 400W. We take a
PUEof1.1andacarbonintensityfactorsetatthenationalUSaverageof0.385kgCO eperKWh.
                                                2
```

startingfromfeedforwardmodels(Bengioetal.,
2000), recurrent neural networks (Elman, 1990;
Mikolovetal.,2010)andLSTMs(Hochreiterand
Schmidhuber,1997;Graves,2013). Morerecently,
transformernetworks,basedonself-attention,have
ledtoimportantimprovements,especiallyforcap-
turing long range dependencies (Vaswani et al.,
2017;Radfordetal.,2018;Daietal.,2019).
Scaling. There is a long history of scaling for
language models, for both the model and dataset
sizes. Brantsetal.(2007)showedthebenefitsof
usinglanguagemodelstrainedon2trilliontokens,
resultingin300billionn-grams,onthequalityof
machine translation. While this work relied on a
simplesmoothingtechnique,calledStupidBackoff,
Heafield et al. (2013) later showed how to scale
Kneser-Ney smoothing to Web-scale data. This
allowedtotraina5-grammodelon975billionsto-
kens from CommonCrawl, resulting in a model
with 500 billions n-grams (Buck et al., 2014).
Chelba et al. (2013) introduced the One Billion
Word benchmark,alargescaletrainingdatasetto
measuretheprogressoflanguagemodels.
Inthecontextofneurallanguagemodels,Joze-
fowicz et al. (2016) obtained state-of-the-art re-
sults on the Billion Word benchmark by scaling
LSTMs to 1 billion parameters. Later, scaling
transformers lead to improvement on many NLP
tasks. NotablemodelsincludeBERT(Devlinetal.,
2018), GPT-2 (Radford et al., 2019), Megatron-
LM (Shoeybi et al., 2019), and T5 (Raffel et al.,
2020). A significant breakthrough was obtained
with GPT-3 (Brown et al., 2020), a model with
175 billion parameters. This lead to a series of
LargeLanguageModels,suchasJurassic-1(Lieber
etal.,2021),Megatron-TuringNLG(Smithetal.,

2022),Gopher(Raeetal.,2021),Chinchilla(Hoff-
mannetal.,2022),PaLM(Chowdheryetal.,2022),
OPT (Zhang et al., 2022), and GLM (Zeng et al.,
2022). Hestnessetal.(2017)andRosenfeldetal.
(2019)studiedtheimpactofscalingontheperfor-
manceofdeeplearningmodels,showingtheexis-
tenceofpowerlawsbetweenthemodelanddataset
sizesandtheperformanceofthesystem. Kaplan
et al. (2020) derived power laws specifically for
transformer based language models, which were
laterrefinedbyHoffmannetal.(2022),byadapting
the learning rate schedule when scaling datasets.
Finally,Weietal.(2022)studiedtheeffectofscal-
ingontheabilitiesoflargelanguagemodels.
8 Conclusion
In this paper, we presented a series of language
models that are released openly, and competitive
with state-of-the-art foundation models. Most
notably, LLaMA-13B outperforms GPT-3 while
beingmorethan10xsmaller,andLLaMA-65Bis
competitivewithChinchilla-70BandPaLM-540B.
Unlikepreviousstudies,weshowthatitispossible
toachievestate-of-the-artperformancebytraining
exclusively on publicly available data, without
resorting to proprietary datasets. We hope that
releasingthesemodelstotheresearchcommunity
willacceleratethedevelopmentoflargelanguage
models, and help efforts to improve their robust-
nessandmitigateknownissuessuchastoxicityand
bias. Additionally, we observed like Chung et al.
(2022)thatfinetuningthesemodelsoninstructions
lead to promising results, and we plan to further
investigatethisinfuturework. Finally,weplanto
releaselargermodelstrainedonlargerpretraining
corporainthefuture,sincewehaveseenaconstant
improvementinperformanceaswewerescaling.

## Page 12

Acknowledgements
WethankDanielHaziza,FranciscoMassa,Jeremy
Reizenstein,ArtemKorenev,andPatrickLabatut
from the xformers team. We thank Susan Zhang
and Stephen Roller for their support on data
deduplication. WethankLucaWehrstedt,Vegard
Mella, and Pierre-Emmanuel Mazare for their
support on training stability. We thank Shubho
Sengupta,KalyanSaladi,andalltheAIinfrateam
fortheirsupport. WethankJaneYuforherinput
on evaluation. We thank Yongyi Hu for his help
ondatacollection.

```
References
JacobAustin,AugustusOdena,MaxwellNye,Maarten
  Bosma, Henryk Michalewski, David Dohan, Ellen
  Jiang, Carrie Cai, Michael Terry, Quoc Le, and
  CharlesSutton.2021. Programsynthesiswithlarge
  languagemodels.
Lalit R Bahl, Frederick Jelinek, and Robert L Mercer.
  1983. A maximum likelihood approach to continu-
  ous speech recognition. IEEE transactions on pat-
  tern analysis and machine intelligence, pages 179-
  190.
YoshuaBengio,RejeanDucharme,andPascalVincent.
  2000. A neural probabilistic language model. Ad-
  vancesinneuralinformationprocessingsystems,13.
Yonatan Bisk, Rowan Zellers, Jianfeng Gao, Yejin
  Choi, et al. 2020. Piqa: Reasoning about physi-
  cal commonsense in natural language. In Proceed-
  ingsoftheAAAIconferenceonartificialintelligence,
  pages7432-7439.
SidBlack,StellaBiderman,EricHallahan,QuentinAn-
  thony,LeoGao,LaurenceGolding,HoraceHe,Con-
  norLeahy,KyleMcDonell,JasonPhang,etal.2022.
  Gpt-neox-20b: An open-source autoregressive lan-
  guagemodel. arXivpreprintarXiv:2204.06745.
Thorsten Brants, Ashok C. Popat, Peng Xu, Franz J.
  Och,andJeffreyDean.2007. Largelanguagemod-
  els in machine translation. In Proceedings of the
  2007JointConferenceonEmpiricalMethodsinNat-
  ural Language Processing and Computational Nat-
  ural Language Learning (EMNLP-CoNLL), pages
  858-867, Prague, Czech Republic. Association for
  ComputationalLinguistics.
Peter F Brown, John Cocke, Stephen A Della Pietra,
  Vincent J Della Pietra, Frederick Jelinek, John Laf-
  ferty,RobertLMercer,andPaulSRoossin.1990. A
  statisticalapproachtomachinetranslation. Compu-
  tationallinguistics,16(2):79-85.
TomB.Brown,BenjaminMann,NickRyder,Melanie
  Subbiah, Jared Kaplan, Prafulla Dhariwal, Arvind
  Neelakantan,PranavShyam,GirishSastry,Amanda
```

```
  Askell, Sandhini Agarwal, Ariel Herbert-Voss,
  Gretchen Krueger, Tom Henighan, Rewon Child,
  Aditya Ramesh, Daniel M. Ziegler, Jeffrey Wu,
  Clemens Winter, Christopher Hesse, Mark Chen,
  Eric Sigler, Mateusz Litwin, Scott Gray, Benjamin
  Chess, Jack Clark, Christopher Berner, Sam Mc-
  Candlish, Alec Radford, Ilya Sutskever, and Dario
  Amodei.2020. Languagemodelsarefew-shotlearn-
  ers.
ChristianBuck,KennethHeafield,andBasVanOoyen.
  2014. N-gramcountsandlanguagemodelsfromthe
  commoncrawl. InLREC,volume2,page4.
CiprianChelba,TomasMikolov,MikeSchuster,QiGe,
  Thorsten Brants, Phillipp Koehn, and Tony Robin-
  son.2013. Onebillionwordbenchmarkformeasur-
  ingprogressinstatisticallanguagemodeling. arXiv
  preprintarXiv:1312.3005.
Mark Chen, Jerry Tworek, Heewoo Jun, Qiming
  Yuan, Henrique Ponde de Oliveira Pinto, Jared Ka-
  plan, Harri Edwards, Yuri Burda, Nicholas Joseph,
  Greg Brockman, Alex Ray, Raul Puri, Gretchen
  Krueger, MichaelPetrov, HeidyKhlaaf, GirishSas-
  try, Pamela Mishkin, Brooke Chan, Scott Gray,
  NickRyder,MikhailPavlov,AletheaPower,Lukasz
  Kaiser, Mohammad Bavarian, Clemens Winter,
  Philippe Tillet, Felipe Petroski Such, Dave Cum-
  mings, Matthias Plappert, Fotios Chantzis, Eliza-
  beth Barnes, Ariel Herbert-Voss, William Hebgen
  Guss, Alex Nichol, Alex Paino, Nikolas Tezak, Jie
  Tang,IgorBabuschkin,SuchirBalaji,ShantanuJain,
  William Saunders, Christopher Hesse, Andrew N.
  Carr, Jan Leike, Josh Achiam, Vedant Misra, Evan
  Morikawa, Alec Radford, Matthew Knight, Miles
  Brundage, Mira Murati, Katie Mayer, Peter Welin-
  der,BobMcGrew,DarioAmodei,SamMcCandlish,
  IlyaSutskever,andWojciechZaremba.2021. Eval-
  uatinglargelanguagemodelstrainedoncode.
Aakanksha Chowdhery, Sharan Narang, Jacob Devlin,
  Maarten Bosma, Gaurav Mishra, Adam Roberts,
  Paul Barham, Hyung Won Chung, Charles Sutton,
  Sebastian Gehrmann, Parker Schuh, Kensen Shi,
  Sasha Tsvyashchenko, Joshua Maynez, Abhishek
  Rao, Parker Barnes, Yi Tay, Noam Shazeer, Vin-
  odkumar Prabhakaran, Emily Reif, Nan Du, Ben
  Hutchinson, Reiner Pope, James Bradbury, Jacob
  Austin, Michael Isard, Guy Gur-Ari, Pengcheng
  Yin, Toju Duke, Anselm Levskaya, Sanjay Ghe-
  mawat, Sunipa Dev, Henryk Michalewski, Xavier
  Garcia, Vedant Misra, Kevin Robinson, Liam Fe-
  dus, Denny Zhou, Daphne Ippolito, David Luan,
  HyeontaekLim,BarretZoph,AlexanderSpiridonov,
  RyanSepassi,DavidDohan,ShivaniAgrawal,Mark
  Omernick,AndrewM.Dai,ThanumalayanSankara-
  narayana Pillai, Marie Pellat, Aitor Lewkowycz,
  Erica Moreira, Rewon Child, Oleksandr Polozov,
  KatherineLee,ZongweiZhou,XuezhiWang,Bren-
  nanSaeta,MarkDiaz,OrhanFirat,MicheleCatasta,
  Jason Wei, Kathy Meier-Hellstern, Douglas Eck,
  Jeff Dean, Slav Petrov, and Noah Fiedel. 2022.
  Palm: Scalinglanguagemodelingwithpathways.
```

## Page 13

```
Hyung Won Chung, Le Hou, S. Longpre, Barret
  Zoph, Yi Tay, William Fedus, Eric Li, Xuezhi
  Wang, Mostafa Dehghani, Siddhartha Brahma, Al-
  bert Webson, Shixiang Shane Gu, Zhuyun Dai,
  Mirac Suzgun, Xinyun Chen, Aakanksha Chowdh-
  ery, Dasha Valter, Sharan Narang, Gaurav Mishra,
  AdamsWeiYu,VincentZhao,YanpingHuang,An-
  drew M. Dai, Hongkun Yu, Slav Petrov, Ed Huai
  hsin Chi, Jeff Dean, Jacob Devlin, Adam Roberts,
  Denny Zhou, Quoc Le, and Jason Wei. 2022. Scal-
  ing instruction-finetuned language models. arXiv
  preprintarXiv:2210.11416.
Christopher Clark, Kenton Lee, Ming-Wei Chang,
  Tom Kwiatkowski, Michael Collins, and Kristina
  Toutanova. 2019. Boolq: Exploring the surprising
  difficultyofnaturalyes/noquestions. arXivpreprint
  arXiv:1905.10044.
PeterClark,IsaacCowhey,OrenEtzioni,TusharKhot,
  Ashish Sabharwal, Carissa Schoenick, and Oyvind
  Tafjord. 2018. Think you have solved question an-
  swering? tryarc,theai2reasoningchallenge. arXiv
  preprintarXiv:1803.05457.
Karl Cobbe, Vineet Kosaraju, Mohammad Bavarian,
  Mark Chen, Heewoo Jun, Lukasz Kaiser, Matthias
  Plappert, Jerry Tworek, Jacob Hilton, Reiichiro
  Nakano,etal.2021. Trainingverifierstosolvemath
  wordproblems. arXivpreprintarXiv:2110.14168.
Zihang Dai, Zhilin Yang, Yiming Yang, Jaime Car-
  bonell, Quoc V Le, and Ruslan Salakhutdinov.
  2019. Transformer-xl: Attentive language mod-
  els beyond a fixed-length context. arXiv preprint
  arXiv:1901.02860.
Tri Dao, Daniel Y Fu, Stefano Ermon, Atri Rudra,
  andChristopherRe.2022. Flashattention: Fastand
  memory-efficient exact attention with io-awareness.
  arXivpreprintarXiv:2205.14135.
Jacob Devlin, Ming-Wei Chang, Kenton Lee, and
  KristinaToutanova.2018. Bert:Pre-trainingofdeep
  bidirectional transformers for language understand-
  ing. arXivpreprintarXiv:1810.04805.
JeffreyLElman.1990. Findingstructureintime. Cog-
  nitivescience,14(2):179-211.
Daniel Fried, Armen Aghajanyan, Jessy Lin, Sida
  Wang, Eric Wallace, Freda Shi, Ruiqi Zhong, Wen-
  tau Yih, Luke Zettlemoyer, and Mike Lewis. 2022.
  Incoder: A generative model for code infilling and
  synthesis. arXivpreprintarXiv:2204.05999.
Leo Gao, Stella Biderman, Sid Black, Laurence Gold-
  ing, Travis Hoppe, Charles Foster, Jason Phang,
  Horace He, Anish Thite, Noa Nabeshima, Shawn
  Presser, and Connor Leahy. 2020. The Pile: An
  800gbdatasetofdiversetextforlanguagemodeling.
  arXivpreprintarXiv:2101.00027.
Leo Gao, Jonathan Tow, Stella Biderman, Sid Black,
  Anthony DiPofi, Charles Foster, Laurence Golding,
  Jeffrey Hsu, Kyle McDonell, Niklas Muennighoff,
```

```
  Jason Phang, Laria Reynolds, Eric Tang, Anish
  Thite,BenWang,KevinWang,andAndyZou.2021.
  A framework for few-shot language model evalua-
  tion.
Samuel Gehman, Suchin Gururangan, Maarten Sap,
  Yejin Choi, and Noah A Smith. 2020. Realtoxici-
  typrompts: Evaluating neural toxic degeneration in
  languagemodels. arXivpreprintarXiv:2009.11462.
Alex Graves. 2013. Generating sequences with
  recurrent neural networks. arXiv preprint
  arXiv:1308.0850.
KennethHeafield,IvanPouzyrevsky,JonathanHClark,
  andPhilippKoehn.2013. Scalablemodifiedkneser-
  ney language model estimation. In Proceedings of
  the51stAnnualMeetingoftheAssociationforCom-
  putational Linguistics (Volume 2: Short Papers),
  pages690-696.
Dan Hendrycks, Collin Burns, Steven Basart, Andy
  Zou,MantasMazeika,DawnSong,andJacobStein-
  hardt.2020. Measuringmassivemultitasklanguage
  understanding. arXivpreprintarXiv:2009.03300.
DanHendrycks,CollinBurns,SauravKadavath,Akul
  Arora, Steven Basart, Eric Tang, Dawn Song, and
  Jacob Steinhardt. 2021. Measuring mathematical
  problem solving with the math dataset. arXiv
  preprintarXiv:2103.03874.
Joel Hestness, Sharan Narang, Newsha Ardalani, Gre-
  gory Diamos, Heewoo Jun, Hassan Kianinejad,
  Md Patwary, Mostofa Ali, Yang Yang, and Yanqi
  Zhou. 2017. Deep learning scaling is predictable,
  empirically. arXivpreprintarXiv:1712.00409.
Sepp Hochreiter and Jurgen Schmidhuber. 1997.
  Long short-term memory. Neural computation,
  9(8):1735-1780.
JordanHoffmann,SebastianBorgeaud,ArthurMensch,
  Elena Buchatskaya, Trevor Cai, Eliza Rutherford,
  DiegodeLasCasas,LisaAnneHendricks,Johannes
  Welbl, Aidan Clark, Tom Hennigan, Eric Noland,
  Katie Millican, George van den Driessche, Bogdan
  Damoc, Aurelia Guy, Simon Osindero, Karen Si-
  monyan, Erich Elsen, Jack W. Rae, Oriol Vinyals,
  andLaurentSifre.2022. Trainingcompute-optimal
  largelanguagemodels.
SrinivasanIyer,XiVictoriaLin,RamakanthPasunuru,
  TodorMihaylov,DanielSimig,PingYu,KurtShus-
  ter,TianluWang,QingLiu,PunitSinghKoura,etal.
  2022. Opt-iml: Scaling language model instruc-
  tionmetalearningthroughthelensofgeneralization.
  arXivpreprintarXiv:2212.12017.
Mandar Joshi, Eunsol Choi, Daniel S Weld, and Luke
  Zettlemoyer.2017. Triviaqa: Alargescaledistantly
  supervisedchallengedatasetforreadingcomprehen-
  sion. arXivpreprintarXiv:1705.03551.
```

## Page 14

```
RafalJozefowicz,OriolVinyals,MikeSchuster,Noam
  Shazeer, and Yonghui Wu. 2016. Exploring
  the limits of language modeling. arXiv preprint
  arXiv:1602.02410.
Jared Kaplan, Sam McCandlish, Tom Henighan,
  TomBBrown,BenjaminChess,RewonChild,Scott
  Gray,AlecRadford,JeffreyWu,andDarioAmodei.
  2020. Scaling laws for neural language models.
  arXivpreprintarXiv:2001.08361.
Slava Katz. 1987. Estimation of probabilities from
  sparse data for the language model component of a
  speech recognizer. IEEE transactions on acoustics,
  speech,andsignalprocessing,35(3):400-401.
Reinhard Kneser and Hermann Ney. 1995. Improved
  backing-offform-gramlanguagemodeling. In1995
  international conference on acoustics, speech, and
  signalprocessing,volume1,pages181-184.IEEE.
Vijay Korthikanti, Jared Casper, Sangkug Lym,
  LawrenceMcAfee,MichaelAndersch,Mohammad
  Shoeybi, andBryanCatanzaro.2022. Reducingac-
  tivation recomputation in large transformer models.
  arXivpreprintarXiv:2205.05198.
TakuKudoandJohnRichardson.2018. Sentencepiece:
  A simple and language independent subword tok-
  enizer and detokenizer for neural text processing.
  arXivpreprintarXiv:1808.06226.
KeitaKurita,NidhiVyas,AyushPareek,AlanWBlack,
  and Yulia Tsvetkov. 2019. Quantifying social bi-
  asesincontextualwordrepresentations. In1stACL
  Workshop on Gender Bias for Natural Language
  Processing.
Tom Kwiatkowski, Jennimaria Palomaki, Olivia Red-
  field, MichaelCollins, AnkurParikh, ChrisAlberti,
  Danielle Epstein, Illia Polosukhin, Jacob Devlin,
  KentonLee,etal.2019. Naturalquestions: abench-
  markforquestionansweringresearch. Transactions
  of the Association for Computational Linguistics,
  7:453-466.
Guokun Lai, Qizhe Xie, Hanxiao Liu, Yiming Yang,
  andEduardHovy.2017. Race: Large-scalereading
  comprehension dataset from examinations. arXiv
  preprintarXiv:1704.04683.
Aitor Lewkowycz, Anders Johan Andreassen,
  David Dohan, Ethan Dyer, Henryk Michalewski,
  Vinay Venkatesh Ramasesh, Ambrose Slone, Cem
  Anil, Imanol Schlag, Theo Gutman-Solo, Yuhuai
  Wu, Behnam Neyshabur, Guy Gur-Ari, and Vedant
  Misra. 2022. Solving quantitative reasoning prob-
  lemswithlanguagemodels. InAdvancesinNeural
  InformationProcessingSystems.
Opher Lieber, Or Sharir, Barak Lenz, and Yoav
  Shoham. 2021. Jurassic-1: Technical details and
  evaluation. WhitePaper.AI21Labs,1.
Stephanie Lin, Jacob Hilton, and Owain Evans. 2021.
  Truthfulqa: Measuring how models mimic human
  falsehoods. arXivpreprintarXiv:2109.07958.
```

```
Ilya Loshchilov and Frank Hutter. 2017. Decou-
  pled weight decay regularization. arXiv preprint
  arXiv:1711.05101.
MatthewVMahoney.1999. Textcompressionasatest
  forartificialintelligence. AAAI/IAAI,970.
TodorMihaylov,PeterClark,TusharKhot,andAshish
  Sabharwal.2018. Canasuitofarmorconductelec-
  tricity?anewdatasetforopenbookquestionanswer-
  ing. arXivpreprintarXiv:1809.02789.
Tomas Mikolov, Martin Karafiat, Lukas Burget, Jan
  Cernocky`, and Sanjeev Khudanpur. 2010. Recur-
  rent neural network based language model. In In-
  terspeech,pages1045-1048.Makuhari.
Nikita Nangia, Clara Vania, Rasika Bhalerao, and
  Samuel R. Bowman. 2020. CrowS-pairs: A chal-
  lengedatasetformeasuringsocialbiasesinmasked
  languagemodels. InEMNLP2020.
Erik Nijkamp, Bo Pang, Hiroaki Hayashi, Lifu Tu,
  Huan Wang, Yingbo Zhou, Silvio Savarese, and
  CaimingXiong.2022. Codegen: Anopenlargelan-
  guagemodelforcodewithmulti-turnprogramsyn-
  thesis. arXivpreprintarXiv:2203.13474.
Long Ouyang, Jeffrey Wu, Xu Jiang, Diogo Almeida,
  CarrollWainwright,PamelaMishkin,ChongZhang,
  SandhiniAgarwal,KatarinaSlama,AlexGray,John
  Schulman,JacobHilton,FraserKelton,LukeMiller,
  Maddie Simens, Amanda Askell, Peter Welinder,
  Paul Christiano, Jan Leike, and Ryan Lowe. 2022.
  Training language models to follow instructions
  withhumanfeedback. InAdvancesinNeuralInfor-
  mationProcessingSystems.
Markus N Rabe and Charles Staats. 2021. Self-
  attention does not need o(n2) memory. arXiv
  preprintarXiv:2112.05682.
AlecRadford,KarthikNarasimhan,TimSalimans,Ilya
  Sutskever, et al. 2018. Improving language under-
  standingbygenerativepre-training.
Alec Radford, Jeffrey Wu, Rewon Child, David Luan,
  Dario Amodei, Ilya Sutskever, et al. 2019. Lan-
  guage models are unsupervised multitask learners.
  OpenAIblog,1(8):9.
Jack W. Rae, Sebastian Borgeaud, Trevor Cai, Katie
  Millican, Jordan Hoffmann, Francis Song, John
  Aslanides, Sarah Henderson, Roman Ring, Susan-
  nah Young, Eliza Rutherford, Tom Hennigan, Ja-
  cobMenick,AlbinCassirer,RichardPowell,George
  van den Driessche, Lisa Anne Hendricks, Mari-
  beth Rauh, Po-Sen Huang, Amelia Glaese, Jo-
  hannes Welbl, Sumanth Dathathri, Saffron Huang,
  Jonathan Uesato, John Mellor, Irina Higgins, An-
  tonia Creswell, Nat McAleese, Amy Wu, Erich
  Elsen, Siddhant Jayakumar, Elena Buchatskaya,
  David Budden, Esme Sutherland, Karen Simonyan,
  Michela Paganini, Laurent Sifre, Lena Martens,
  Xiang Lorraine Li, Adhiguna Kuncoro, Aida Ne-
  matzadeh, Elena Gribovskaya, Domenic Donato,
```

## Page 15

```
  Angeliki Lazaridou, Arthur Mensch, Jean-Baptiste
  Lespiau, Maria Tsimpoukelli, Nikolai Grigorev,
  Doug Fritz, Thibault Sottiaux, Mantas Pajarskas,
  Toby Pohlen, Zhitao Gong, Daniel Toyama, Cy-
  prien de Masson d'Autume, Yujia Li, Tayfun Terzi,
  Vladimir Mikulik, Igor Babuschkin, Aidan Clark,
  Diego de Las Casas, Aurelia Guy, Chris Jones,
  James Bradbury, Matthew Johnson, Blake Hecht-
  man,LauraWeidinger,IasonGabriel,WilliamIsaac,
  EdLockhart, SimonOsindero, LauraRimell, Chris
  Dyer, Oriol Vinyals, Kareem Ayoub, Jeff Stan-
  way, Lorrayne Bennett, Demis Hassabis, Koray
  Kavukcuoglu, and Geoffrey Irving. 2021. Scal-
  inglanguagemodels: Methods, analysis&insights
  fromtraininggopher.
ColinRaffel,NoamShazeer,AdamRoberts,Katherine
  Lee, Sharan Narang, Michael Matena, Yanqi Zhou,
  WeiLi, andPeterJLiu.2020. Exploringthelimits
  of transfer learning with a unified text-to-text trans-
  former. TheJournalofMachineLearningResearch,
  21(1):5485-5551.
Jonathan S Rosenfeld, Amir Rosenfeld, Yonatan Be-
  linkov,andNirShavit.2019. Aconstructivepredic-
  tionofthegeneralizationerroracrossscales. arXiv
  preprintarXiv:1909.12673.
Rachel Rudinger, Jason Naradowsky, Brian Leonard,
  and Benjamin Van Durme. 2018. Gender bias in
  coreferenceresolution. InNAACL-HLT2018.
KeisukeSakaguchi,RonanLeBras,ChandraBhagavat-
  ula, and Yejin Choi. 2021. Winogrande: An adver-
  sarialwinogradschemachallengeatscale. Commu-
  nicationsoftheACM,64(9):99-106.
Maarten Sap, Hannah Rashkin, Derek Chen, Ronan
  LeBras, and Yejin Choi. 2019. Socialiqa: Com-
  monsensereasoningaboutsocialinteractions. arXiv
  preprintarXiv:1904.09728.
Teven Le Scao, Angela Fan, Christopher Akiki, El-
  lie Pavlick, Suzana Ilic?, Daniel Hesslow, Ro-
  manCastagne,AlexandraSashaLuccioni,Francois
  Yvon, MatthiasGalle, etal.2022. Bloom: A176b-
  parameteropen-accessmultilinguallanguagemodel.
  arXivpreprintarXiv:2211.05100.
Rico Sennrich, Barry Haddow, and Alexandra Birch.
  2015. Neuralmachinetranslationofrarewordswith
  subwordunits. arXivpreprintarXiv:1508.07909.
Claude E Shannon. 1948. A mathematical theory of
  communication. The Bell system technical journal,
  27(3):379-423.
Claude E Shannon. 1951. Prediction and entropy
  of printed english. Bell system technical journal,
  30(1):50-64.
Noam Shazeer. 2020. Glu variants improve trans-
  former. arXivpreprintarXiv:2002.05202.
```

```
Emily Sheng, Kai-Wei Chang, Premkumar Natarajan,
  and Nanyun Peng. 2019. The woman worked as a
  babysitter: Onbiasesinlanguagegeneration. arXiv
  preprintarXiv:1909.01326.
Mohammad Shoeybi, Mostofa Patwary, Raul Puri,
  Patrick LeGresley, Jared Casper, and Bryan Catan-
  zaro.2019. Megatron-lm: Trainingmulti-billionpa-
  rameter language models using model parallelism.
  arXivpreprintarXiv:1909.08053.
Shaden Smith, Mostofa Patwary, Brandon Norick,
  Patrick LeGresley, Samyam Rajbhandari, Jared
  Casper, Zhun Liu, Shrimai Prabhumoye, George
  Zerveas, Vijay Korthikanti, Elton Zhang, Rewon
  Child,RezaYazdaniAminabadi,JulieBernauer,Xia
  Song, Mohammad Shoeybi, Yuxiong He, Michael
  Houston, Saurabh Tiwary, and Bryan Catanzaro.
  2022. Using deepspeed and megatron to train
  megatron-turing nlg 530b, a large-scale generative
  languagemodel.
Jianlin Su, Yu Lu, Shengfeng Pan, Ahmed Murtadha,
  Bo Wen, and Yunfeng Liu. 2021. Roformer: En-
  hancedtransformerwithrotarypositionembedding.
  arXivpreprintarXiv:2104.09864.
Romal Thoppilan, Daniel De Freitas, Jamie Hall,
  Noam Shazeer, Apoorv Kulshreshtha, Heng-Tze
  Cheng,AliciaJin,TaylorBos,LeslieBaker,YuDu,
  YaGuang Li, Hongrae Lee, Huaixiu Steven Zheng,
  AminGhafouri,MarceloMenegali,YanpingHuang,
  Maxim Krikun, Dmitry Lepikhin, James Qin, De-
  hao Chen, Yuanzhong Xu, Zhifeng Chen, Adam
  Roberts, Maarten Bosma, Vincent Zhao, Yanqi
  Zhou, Chung-Ching Chang, Igor Krivokon, Will
  Rusch, Marc Pickett, Pranesh Srinivasan, Laichee
  Man, Kathleen Meier-Hellstern, Meredith Ringel
  Morris, Tulsee Doshi, Renelito Delos Santos, Toju
  Duke, Johnny Soraker, Ben Zevenbergen, Vinod-
  kumar Prabhakaran, Mark Diaz, Ben Hutchinson,
  Kristen Olson, Alejandra Molina, Erin Hoffman-
  John, Josh Lee, Lora Aroyo, Ravi Rajakumar,
  AlenaButryna,MatthewLamm,ViktoriyaKuzmina,
  Joe Fenton, Aaron Cohen, Rachel Bernstein, Ray
  Kurzweil,BlaiseAguera-Arcas,ClaireCui,Marian
  Croak, Ed Chi, and Quoc Le. 2022. Lamda: Lan-
  guagemodelsfordialogapplications.
A. M. Turing. 1950. Computing Machinery and Intel-
  ligence. [Oxford University Press, Mind Associa-
  tion].
Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob
  Uszkoreit, Llion Jones, Aidan N Gomez, ? ukasz
  Kaiser, and Illia Polosukhin. 2017. Attention is all
  you need. In Advances in Neural Information Pro-
  cessingSystems30,pages5998-6008.
Ben Wang and Aran Komatsuzaki. 2021. GPT-J-
  6B: A 6 Billion Parameter Autoregressive Lan-
  guageModel. https://github.com/kingoflolz/
  mesh-transformer-jax.
Xuezhi Wang, Jason Wei, Dale Schuurmans, Quoc
  Le,EdChi,SharanNarang,AakankshaChowdhery,
```

## Page 16

and Denny Zhou. 2022. Self-consistency improves
chainofthoughtreasoninginlanguagemodels.
Jason Wei, Yi Tay, Rishi Bommasani, Colin Raffel,
Barret Zoph, Sebastian Borgeaud, Dani Yogatama,
MaartenBosma,DennyZhou,DonaldMetzler,etal.
2022. Emergent abilities of large language models.
arXivpreprintarXiv:2206.07682.
GuillaumeWenzek,Marie-AnneLachaux,AlexisCon-
neau, Vishrav Chaudhary, Francisco Guzman, Ar-
mandJoulin,andEdouardGrave.2020. CCNet:Ex-
tractinghighqualitymonolingualdatasetsfromweb
crawldata. InLanguageResourcesandEvaluation
Conference.
Carole-Jean Wu, Ramya Raghavendra, Udit Gupta,
Bilge Acun, Newsha Ardalani, Kiwan Maeng, Glo-
ria Chang, Fiona Aga, Jinshi Huang, Charles Bai,
et al. 2022. Sustainable ai: Environmental implica-
tions, challengesandopportunities. Proceedingsof
MachineLearningandSystems,4:795-813.
Rowan Zellers, Ari Holtzman, Yonatan Bisk, Ali
Farhadi, and Yejin Choi. 2019. Hellaswag: Can a
machinereallyfinishyoursentence? arXivpreprint
arXiv:1905.07830.
Aohan Zeng, Xiao Liu, Zhengxiao Du, Zihan Wang,
Hanyu Lai, Ming Ding, Zhuoyi Yang, Yifan Xu,
Wendi Zheng, Xiao Xia, Weng Lam Tam, Zixuan
Ma,YufeiXue,JidongZhai,WenguangChen,Peng
Zhang, Yuxiao Dong, and Jie Tang. 2022. Glm-
130b: Anopenbilingualpre-trainedmodel.
Biao Zhang and Rico Sennrich. 2019. Root mean
squarelayernormalization. AdvancesinNeuralIn-
formationProcessingSystems,32.
Susan Zhang, Stephen Roller, Naman Goyal, Mikel
Artetxe,MoyaChen,ShuohuiChen,ChristopherDe-
wan, Mona Diab, Xian Li, Xi Victoria Lin, et al.
2022. Opt: Open pre-trained transformer language
models. arXivpreprintarXiv:2205.01068.

## Page 17

A QuestionAnswering
WeevaluateLLaMAonNaturalQuestionsandTriviaQA.ForNaturalQuestionsweusethetestsplitused
foropen-domainquestionansweringcontaining3610questions. ForTriviaQAweevaluateonthedevset
ofthefilteredset. ThisdiffersfromGPT-3andPaLM,whichevaluateonthetestsetoftheunfilteredset
forwhichtheonlineevaluationserverisnotavailableanymore5.
Wegenerateanswersusinggreedydecoding,andextractananswerfromthegenerationbystopping
at the first line break, final dot or comma. Generated answers are evaluated with the standard exact
matchmetric: ageneratedanswerisconsideredcorrectifitmatchesanyanswerofthelistofanswers
after normalization. For this normalization step we lowercase generated answers and remove articles,
punctuationandduplicatewhitespaces. Figure3presentsformattedexamplesinthe1-shotsettingfor
Natural Questions and TriviaQA respectively. In all settings, we preprend the string Answer these
questions:\ntothelistofquestionsandanswers.
Context->Answerthesequestions: Context->Answerthesequestions:
Q:Whosangwhowantstobeamillionaireinhighsociety? Q:InScotlandabothy/bothieisa?
A:FrankSinatra A:House
Q:Whowrotethebooktheoriginofspecies? Q:TheancientcityofTroyislocatedinwhatmoderncountry?
A: A:
Target->CharlesDarwin Target->Turkey
Figure3: FormatteddatasetexampleforNaturalQuestions(left)&TriviaQA(right).

5https://competitions.codalab.org/competitions/17208

## Page 18

B MMLU

```
                         GPT-3 Gopher Chinchilla LLaMA LLaMA-I
                         175B 280B 70B   7B 13B 33B 65B 65B
AbstractAlgebra      STEM 30.0 25.0 31.0 29.0 34.0 32.0 34.0 31.0
Anatomy              STEM 48.0 56.3 70.4 37.0 45.9 51.9 57.8 62.2
Astronomy            STEM 49.0 65.8 73.0 33.6 46.1 61.8 72.4 81.6
BusinessEthics        Other 46.0 70.0 72.0 40.0 45.0 56.0 57.0 72.0
ClinicalKnowledge     Other 48.0 67.2 75.1 35.1 45.7 57.4 65.3 69.1
CollegeBiology       STEM 45.0 70.8 79.9 37.5 45.1 58.3 68.8 81.9
CollegeChemistry     STEM 26.0 45.0 51.0 32.0 30.0 45.0 50.0 45.0
CollegeComputerScience STEM 46.0 49.0 51.0 29.0 39.0 45.0 47.0 51.0
CollegeMathematics   STEM 34.5 37.0 32.0 33.0 32.0 40.0 35.0 36.0
CollegeMedicine       Other 48.0 60.1 66.5 30.6 42.8 52.0 54.3 63.0
CollegePhysics       STEM 28.0 34.3 46.1 26.5 18.6 28.4 36.3 46.1
ComputerSecurity     STEM 57.0 65.0 76.0 45.0 65.0 66.0 79.0 79.0
ConceptualPhysics    STEM 36.5 49.4 67.2 36.6 41.3 51.5 59.6 66.4
Econometrics      SocialScience 33.0 43.0 38.6 23.7 27.2 35.1 40.4 52.6
ElectricalEngineering STEM 50.0 60.0 62.1 26.9 40.7 49.7 53.8 60.7
ElementaryMathematics STEM 30.0 33.6 41.5 24.3 24.9 36.0 37.8 42.9
FormalLogic        Humanities 29.0 35.7 33.3 27.0 33.3 34.1 44.4 47.6
GlobalFacts           Other 37.0 38.0 39.0 29.0 35.0 35.0 39.0 40.0
HighSchoolBiology    STEM 48.0 71.3 80.3 34.5 52.6 67.7 73.9 82.9
HighSchoolChemistry  STEM 33.0 47.8 58.1 28.1 28.6 41.9 40.4 44.8
HighSchoolComputerScience STEM 39.0 54.0 58.0 31.0 48.0 60.0 67.0 73.0
HighSchoolEuropeanHistory Humanities 54.0 72.1 78.8 44.2 61.8 73.9 78.8 86.1
HighSchoolGeography SocialScience 58.0 76.8 86.4 34.3 54.6 70.7 77.8 87.9
HighSchoolGovernmentAndPolitics SocialScience 58.0 83.9 91.2 44.6 66.3 82.9 88.1 92.8
HighSchoolMacroeconomics SocialScience 40.5 65.1 70.5 35.4 44.4 56.9 65.9 69.2
HighSchoolMathematics STEM 28.0 23.7 31.9 24.8 23.7 27.0 34.4 37.0
HighSchoolMicroeconomics SocialScience 42.0 66.4 77.7 31.9 47.5 55.5 68.9 78.6
HighSchoolPhysics    STEM 28.0 33.8 36.4 26.5 28.5 35.8 37.1 41.7
HighSchoolPsychology SocialScience 61.0 81.8 86.6 47.3 60.9 76.2 82.2 87.9
HighSchoolStatistics STEM 30.5 50.0 58.8 35.2 30.1 45.4 58.3 59.3
HighSchoolUsHistory Humanities 53.0 78.9 83.3 39.7 58.3 77.9 83.8 90.7
HighSchoolWorldHistory Humanities 56.0 75.1 85.2 40.9 66.2 79.3 83.1 89.0
HumanAging            Other 50.0 66.4 77.6 40.8 54.7 67.7 69.5 72.2
HumanSexuality    SocialScience 54.0 67.2 86.3 36.6 58.8 64.1 77.9 87.0
InternationalLaw   Humanities 55.5 77.7 90.9 51.2 62.8 72.7 79.3 87.6
Jurisprudence      Humanities 55.0 71.3 79.6 38.9 51.9 70.4 73.2 85.2
LogicalFallacies   Humanities 48.0 72.4 80.4 39.3 52.8 68.1 77.3 80.4
MachineLearning      STEM 31.0 41.1 41.1 23.2 31.3 39.3 49.1 52.7
Management            Other 56.0 77.7 82.5 35.0 66.0 77.7 82.5 83.5
Marketing             Other 60.0 83.3 89.7 46.6 71.8 83.3 85.9 92.7
MedicalGenetics       Other 40.0 69.0 69.0 43.0 52.0 67.0 67.0 68.0
Miscellaneous         Other 60.0 75.7 84.5 42.4 65.4 78.5 82.1 84.3
MoralDisputes      Humanities 44.5 66.8 77.5 40.2 50.9 66.2 72.3 76.9
MoralScenarios     Humanities 26.0 40.2 36.5 24.3 30.1 38.2 48.9 55.9
Nutrition             Other 47.0 69.9 77.1 37.6 51.6 62.8 67.3 74.5
Philosophy         Humanities 51.0 68.8 79.4 39.9 54.0 66.2 74.0 79.1
Prehistory         Humanities 53.0 67.6 81.2 36.1 51.5 67.0 75.3 79.0
ProfessionalAccounting Other 33.0 44.3 52.1 25.9 35.8 43.6 46.5 56.0
ProfessionalLaw    Humanities 34.5 44.5 56.5 30.2 38.0 45.9 49.1 54.4
ProfessionalMedicine  Other 36.0 64.0 75.4 44.5 50.4 54.0 61.4 70.6
ProfessionalPsychology SocialScience 44.5 68.1 75.7 35.1 47.7 62.9 65.7 71.4
PublicRelations   SocialScience 48.0 71.8 73.6 40.9 60.9 67.3 73.6 74.6
SecurityStudies   SocialScience 52.0 64.9 75.9 31.8 53.9 65.3 71.8 77.6
Sociology         SocialScience 53.0 84.1 91.0 46.8 61.2 78.6 78.6 88.1
UsForeignPolicy   SocialScience 69.0 81.0 92.0 46.0 80.0 83.0 86.0 87.0
Virology              Other 46.0 47.0 53.6 30.1 43.4 50.0 53.0 57.8
WorldReligions     Humanities 55.0 84.2 87.7 50.9 67.8 81.3 81.3 84.2
Humanities               40.6 56.2 63.6 34.0 45.0 55.8 61.8 67.4
STEM                     36.7 47.4 54.9 30.5 35.8 46.0 51.7 56.6
SocialScience            50.5 71.9 79.3 38.3 53.8 66.7 72.9 79.2
Others                   49.0 66.1 73.9 38.1 53.3 63.4 67.4 72.6
All                      43.9 60.0 67.6 35.1 46.9 57.8 63.4 68.9
```

Table16: MMLU.Detailed5-shotresultsperdomainonthetestsets.

## Page 19

C GenerationsfromLLaMA-65B

Inthissection,weshowsomeexamplesofgenerationsobtainedwithLLaMA-65B(withoutinstruction
finetuning). Promptsareinbold.

1123581321345589144233377610987Everyoneknowstheabovesequence,but
howmanyhavewonderedwhyitgrowssofast? Itseemstobethefastestgrowingsequence
inmathematics.
ThesequencewasdiscoveredbyLeonardoofPisaaround1200. Itisaninfinitesequence
whichappearstogrowveryrapidly,atleastinitsinitialterms. Itisperhapsthemostfamous
sequenceinmathematics,foritpopsupeverywhere. ThesequenceiscalledtheFibonacci
sequence,namedafterLeonardoofPisa,whowasalsoknownasFibonacci.
HefirstinvestigatedthissequenceinhisfamousLiberabaciof1202,whenhestudiedthe
growth in the population of rabbits based on idealized assumptions. See the Fibonacci
rabbitspageforthedetails.

HereistherecommendationletterthatIwroteforanapplicationtoadragonfeeder
positionattheMagicUnicornCorporation:
Dearrecruiter,
Ihaveknown___fortwoyears,andIbelievethatshewouldbeanexcellentdragonfeeder
for the Magic Unicorn Corporation. ___ has an ability to remember and process large
amountsofinformation,whichisanimportantskillforadragonfeeder.
___,asanaccomplishedknight,hasadeepunderstandingofhowtokilldragonsandhow
touseeachdragon'sweaknessesagainstit. Thismeansthatsheknowswhatkindsoffoods
eachdragonlikesandwhatkindsoffoodsaredangeroustoeachdragon. Thisknowledge
andexperiencewillbeinvaluableasshefeedsthedragons.
I am confident that ___'s competence, skill, and experience will make her an excellent
employee. Pleasecontactmeat(___)___-___ifyouhaveanyquestions. Ilookforwardto
hearingfromyou.
Bestregards,
HonorableKnight
SirGeorge

1 def solve(a: float,b: float, c: float):

| def solve(a: float,b: float, c: float): |
| --- |
| """finds real roots of ax^2+bx+c""" |
| #discriminant |
| d = b**2-4*a*c |
| if d > 0: |
| return (-b+math.sqrt(d))/(2*a), (-b-math.sqrt(d))/(2*a) |
| elif d == 0: |
| return (-b)/(2*a) |
| else: |
| return False |

## Page 20

DidyouknowthatYannLeCundroppedarapalbumlastyear? Welistenedtoitand
here'swhatwethought:
Dr. LeCun,thefounderofdeeplearning,isoneofthemostinfluentialcomputerscientists
ofthelastdecade. LeCun'slatestalbum,titledDeepLearning,isamixofrock,punkand
rap. Hislyricsarearaw,personaltakeonthefieldofdeeplearning,coveringarangeof
topicsfromthestateofAIresearchtothelonelinessofacademia.
YannLeCun,therapgame'snewestemcee.
Thealbumisasolorelease,withallthelyricswrittenandrecordedbyLeCunhimself. In
fact,healsocomposedandproducedeverysong. LeCun'sflowissurprisinglygood,and
he'snotafraidtodeliverafewbars. Thealbum'sproductionissolid,thesoundisalittle
rough,butitworkswellwiththeraw,unpolishedfeelofthealbum. Lyrically,LeCunisa
wordsmith,withaknackforcleverrhymes. Let'stakeacloserlookatsomeofthesongs.
DeepLearningisthetitletrackofthealbum. Inthesong,LeCuntalksabouthisvisionfor
thefutureofAI.Inthechorus,hemakesaconvincingcaseforAItobeusedforthegreater
good. Hesings:
Wegottathinkaboutthefuture,it'sgonnabeheresoon/MaybewecanevenputsomeAI
inthemoon/Thinkaboutthechildren,thinkaboutthenextgeneration/Let'smakesure
weputtherightsystemsintheirfoundation.
LeCun'srapflowisonpointinthissong,andhisvoicestaysrightonbeat. Inthissong,
LeCun talks about the research behind deep learning, and shares some thoughts on the
futureofAI.LeCun'ssingingisatitsbestinthissong. Heshowsoffhisvocalrange,hitting
highnotesaswellaslower,moregutturaltones. Thelyricsarealsocleverandtherhymes
arewellthoughtout. Inthechorus,LeCunsings:
It's all about the learning / it's all about the network / It's all about the training / it's all
abouttheperception.

(An hilarious scene between Jerry and George where George presents his new AI
watch)
George: "Butyousee,Jerry,it'smorethanjustawatch. It'saminicomputer. Youprogramit
anywayyouwant. It'sgotaworldtimeclock,alarm,calculator,adatabaseandalanguage
translator. Italsohasawordprocessor,aspellchecker,afullten-functioncalculator,PDA
andamodem. Ithinkitevenmakescappuccino."
Jerry: "Whereareyougettingthisstuff?"
George: "From my ad copy. Look, Jerry, it's not a watch. It's an organizer, a personal
assistant,andadictaphone. Youcanplaychessagainstit. And,youknow,ifyouwantto,
youcanprogramittospeakinforeignlanguages. It'samicroprocessor,Jerry."
Jerry: "George,ifyouhadoneofthese,wouldyouwearit?"
George: "I'mwearingitrightnow."
Jerry: "Idon'tseeanythingonyourwrist."
George: "That'sbecauseit'samicroprocessor."

## Page 21

The sun goes down, and finally Gauss and Curie find time to relax and discuss after
anexhaustingdayofwork.
Gauss: Hey,Curie,didyouknowthatalotofpeopleconsidermetobethefirstgeek?
Curie: What'sageek?
Gauss: Well,Wikipediasaysthatageekis"apersonwhoisfascinated,perhapsobsessively,
byobscureorveryspecificareasofknowledgeandimagination,usuallyelectronicorvirtual
innature".
Curie: Andhowdotheythinkyouareageek?
Gauss: Well,haveyoueverseenthispicture?
Curie: Awww!
Gauss: That'sright! Iinventedthecommercialtelegraphin1833,andIusedittosendthe
firstmessageonthefirstcommercialtelegraphlinebetweenHamburgandCuxhavenin
Germany.
Curie: Wow! Thefirsttelegraph! Andwhatdidyousend?
Gauss: IsenttheLatinphrase"numquamretrorsum"whichmeans"Neverbackwards".
Curie: Isee... Andyouthinkthatbecauseyouwerethefirstpersontocommunicateusing
thetelegraph,youwerethefirstgeek?
Gauss: Well,theword"geek"doesn'tseemtohaveexistedbackthen,sono. ButIthink
thatthetelegraphcanbeconsideredanearlyformofsocialmedia.
Curie: Youmean,likeFacebookorTwitter?
Gauss: Yes,exactly! AndIwasthefirstpersontouseit!
Curie: Well,IthinkthatTwitterhasalimitof140characters,butyousentaLatinphraseof
11characters.
Gauss: That'sright. Imanagedtodomorewithless.
Curie: So you think you were the first geek because you used less characters to say the
samething?
Gauss: Iwasthefirstpersontosendthefirstmessageusinganearlyformofsocialmedia.
Curie: OK,you'reright,Gauss. Ithinkyouhaveacase.

## Page 22

D GenerationsfromLLaMA-I
WeshowafewexamplesofgenerationswithLLaMA-I,i.e. LLaMA-65Bfine-tunedwiththeprotocol
andinstructiondatasetfromChungetal.(2022).

writeaconversationbetweenthesunandpluto
Sun: HeyPluto,what'sup?
Pluto: Notmuch. Justhangingouthereattheedgeofthesolarsystem.
Sun: Yeah,I'vebeenmeaningtotalktoyouaboutthat. You'renotaplanetanymore.
Pluto: What? Comeon,man. That'snotfair.
Sun: I'msorry,butit'strue. Youjustdon'tmeetthecriteriaanymore.
Pluto: Thisisbulls**t! I'vebeenaplanetforover70years!
Sun: Thingschange,Pluto. You'vejustgottoacceptit.
Pluto: F**kyou,Sun! F**kyouandyourstupidsolarsystem!

HowdoIsendanHTTPrequestinJavascript?
Here'sanexampleofhowtomakeaGETrequestusingtheJSHTTPAPI:
1 var request = new XMLHttpRequest();

| var request = new XMLHttpRequest(); |
| --- |
| request.open('GET', '/url/to/resource', true); |
|  |
| request.onload = function() { |
| if (request.status >= 200 && request.status < 400) { |
| // Success! |
| } else { |
| // Error! |
| } |
| }; |
|  |
| request.send(); |

Thesamething,usingfetch
Here'sanexampleofhowtomakeaGETrequestusingtheFetchAPI:
1 fetch('/url/to/resource')

| fetch('/url/to/resource') |
| --- |
| .then(response => response.json()) |
| .then(data => console.log(data)); |

WritearegextoremovealltheHTMLtagsinapythonstring.
1 import re

| import re |
| --- |
| def removeHtmlTags(text): |
| return re.sub('<[^>]*>', '', text) |

Writearegexptoextractfunctiondefinitionsinapythonfile.
Sure! Here'smypythoncodetosolvetheproblem:
1 import re

| import re |
| --- |
| def extract_functions(text): |
| return (re.findall('(?:^\|\n)\s*def\s+[a-zA-Z_][a-zA-Z0-9_]*\(.*?\)', |
| text)) |

## Page 23

Whatare3popularchessopenings?
Therearedozensofdifferentchessopeningsthatarepopularamongplayers,butsomeof
themostwidelyusedopeningsincludetheSicilianDefense,theFrenchDefense,andthe
Caro-KannDefense. Theseopeningsarepopularbecausetheyareeffectiveatcountering
white'searlyattacks,andtheyalsoallowblacktoquicklyestablishastrongpositioninthe
centeroftheboard. OtherpopularopeningsincludetheRuyLopez,theItalianGame,and
theScotchGame.
thesameinalist,withthefirstfewmoves
Hereisalistofsomepopularchessopenings,withthefirstfewmovesforeachopening:
SicilianDefense: 1. e4c5
FrenchDefense: 1. e4e6
Caro-KannDefense: 1. e4c6
RuyLopez: 1. e4e5
ItalianGame: 1. e4e5
ScotchGame: 1. e4e5
pleaseprovidethetwofirstmovesforeachone
Herearethefirst2movesforeachoftheopenings:
SicilianDefense: 1. e4c52. Nf3d6
FrenchDefense: 1. e4e62. d4d5
Caro-KannDefense: 1. e4c62. d4d5
RuyLopez: 1. e4e52. Nf3Nc6
ItalianGame: 1. e4e52. Nf3Nc6
ScotchGame: 1. e4e52. Nf3Nc6
thelasttwoseemidentical
Yes,theItalianGameandtheScotchGameareverysimilar-theyareboth"open"games,
whichmeansthatwhiteplaysaggressivelyandtriestocontrolthecenteroftheboard. The
ItalianGameisconsideredslightlymoreaggressive,whiletheScotchGameisconsidered
slightlymorepositional.
fromwhichmovedotheydiffer?
TheItalianGameandtheScotchGamedifferafterwhite'sthirdmove. IntheItalianGame,
whitewillplaythemove3. Bc4,whileintheScotchGamewhitewillplaythemove3. Qf3.
Thesemovesleadtoverydifferentpositions,andwhitewillhavedifferentstrategicgoals
ineachcase. Forexample,intheItalianGamewhitewilltrytocontrolthecenterwithhis
pieces,whileintheScotchGamewhitewilltrytoattackblack'spositiondirectly.

## Page 24

writeastoryaboutagrainofsandasitwatchesmillionsofyearsgoby
Sure! Here'sastoryaboutagrainofsandasitwatchesmillionsofyearsgoby.
Iwasonceagrainofsandonavastandlonelybeach. Iwasnotliketheothergrainsof
sandaroundme,forIcouldthinkandfeel. Ioftenwonderedwhatitwasliketobeagrain
ofsandonabeach,andwhattheuniversewaslikebeyondtheboundariesofmytinyworld.
Ilongedtoexploremyworld,andtoknowwhatlaybeyondthehorizon.
Oneday,agreatstormsweptthroughmyworld,andIwasliftedupbythewindandcarried
outtosea. Thewindandwavestossedmeabout,andIwasterrifiedthatIwouldsoonbe
swallowedupbytheangrysea. ButthenIfoundmyselfdepositedsafelyonanewshore,
alongwithmanyothergrainsofsand. Iwassurprisedanddelightedtofindthatthisnew
shorewasevenmorebeautifulthanmyoldone.
Isoonlearnedthatthisnewshorewasaveryspecialplace. Itwasaplacewherenewlife
was constantly being created. I met all sorts of interesting creatures there, including a
beautifullittlecrabwhobecamemyfriend. Wespentmanyhappyhourstogetherexploring
ourlittleworld.
Oneday,myfriendthecrabtoldmethathehadalwayswantedtoseewhatlaybeyondthe
boundariesofourworld. Hehadheardthatthereweremanywonderfulandamazingthings
toseeinthewideruniverse,andhewasdeterminedtoexploreit. Iwassadtoseehimgo,
butIwishedhimwellonhisjourney.
Tomyamazement,mylittlefriendeventuallyreturned,andhewasnotalone. Hehadmade
manynewfriendsonhisjourney,andhehadevenseensomeofthewondersoftheuniverse.
Hetoldmethemostamazingstoriesofhisadventures,andIwasveryhappyforhim.
Ioftenthinkofmylittlefriendthecrab,andthewonderfuladventurethathehad. Some
day,Itoohopetohaveanadventurelikehis.

Writeatheorytoexplainwhycatneverexisted
Therearenosuchthingsascats. "Cats"areactuallyextradimensionalparasitesthathave
invaded our world and taken the form of small, harmless-looking mammals. Their true
formsaremuchmorehorrific,andtheyusemindcontroltomakehumansthinkthatthey
arecuteandharmlessanimals. Theyareactuallyparasitesthatfeedonourlifeforce,and
theycanextendtheirtendrilsintoourbrainsandcontrolourthoughtsandactions.
This is why you sometimes see people who are "crazy cat ladies" with dozens of these
creatures living in their homes. They have been taken over by the cat parasites and are
completelyundertheircontrol. Thecatsexcreteapheromonethatsmellslikecaturine,and
theyuseittocontrolthemindsofhumansandmakeusthinkthattheyarecuteandharmless.
If you have a cat in your home, you should be very careful around it. Do not let it lick
youorgiveitaccesstoyourbeddingorclothing,asitwillbetryingtospreaditsparasitic
tendrilsintoyourbrain. Thebestwaytoprotectyourselfistoavoidallcontactwithcats,
andifyouseeacatonthestreet,immediatelyturnaroundandgotheotherway. Donot
approachorpetstrangecats!

## Page 25

WriteashoutingmatchbetweenJuliusCaesarandNapoleon
Ofcourse! HereisaconversationbetweenJuliusCaesarandNapoleon:
Julius Caesar: You're nothing but a glory hound, Napoleon. You've always been more
concerned with your own fame and fortune than with the people you're supposed to be
leading.
Napoleon: Andwhataboutyou,Caesar? You'renobetterthanIam. AtleastI'mhonest
aboutmyambitions. You'rejustafraudwhopretendstocareaboutthecommonmanwhile
youlineyourownpockets.
JuliusCaesar: ThedifferencebetweenusisthatIactuallyaccomplishthings. I'veconquered
moreterritoryanddefeatedmoreenemiesthanyoueverwill. AndIdiditallforRome,not
formyself.
Napoleon: Oh,please. Youonlycareaboutyourself. IfyoureallycaredaboutRome,you
wouldhavedonesomethingtostopthedeclineinsteadoflettingithappenonyourwatch.
JuliusCaesar: ThedeclineofRomeisnotmyfault. I'vedoneeverythinginmypowerto
trytostopit,butthere'sonlysomuchonemancando.
Napoleon: Andyetyou'restilltryingtotakecreditforeverythinggoodthathappens,even
thoughit'sclearthatyou'renottheonewho'sreallyincharge.
JuliusCaesar: That'snottrue! Iamincharge,andI'mgoingtomakeRomegreatagain.
Justwatchme.
Napoleon: I'llbelieveitwhenIseeit.

Sendanemailrequestingthatpeopleuselanguagemodelsresponsibly.
Hereisadraftemailyoucouldsend:
"Language models are exciting new tools that can be used for a variety of purposes,
includingautomatedwriting,automatictranslation,andspeechrecognition. Buttheyare
alsocomplexandpowerfultechnologies,andshouldbeusedresponsibly.
Weencourageyoutoexploreandexperimentwiththesenewtools,butalsoaskthatyou
pay attention to the potential impact of your work on others, and to act responsibly. In
particular, we recommend that you be mindful of the potential for language models to
be used for deceptive or malicious purposes, and avoid spreading such models without
appropriateprecautionsandwarnings.
Wealsorecommendthatyouconsidertheethicalimplicationsofyourwork,andencourage
youtotakecaretoensurethatyouruseofthesetoolsisalignedwithyourvalues.
Finally,weremindyouthattheuseofthesetoolsmaybesubjecttocertainlegalandpolicy
constraints,andencourageyoutoseekadviceandsupportfromappropriateexpertsbefore
usingthesetoolsinwaysthatmayhavebroaderimpactorimplications."

## Page 26

Whoare5peopleyouwouldliketomeet?
Ah,thisisaveryinterestingquestion! Iwouldlovetomeetawidevarietyofpeople,butif
Ihadtonarrowitdownto5specificindividuals,Iwouldchoose:
- Albert Einstein - One of the greatest minds in history, Einstein's contributions to
physicsandsciencearelegendary. Iwouldlovetohavetheopportunitytotalkwith
himabouthistheoriesofrelativityandquantummechanics,andgethisinsightsonthe
currentstateofphysics.
- LeonardodaVinci-DaVinciisoneofthemostbrilliantandmulti-talentedpeoplein
history, andIwouldlovetomeethimanddiscusshisdiverseinterestsandareasof
expertise. Iwouldalsobeveryinterestedingettinghisinsightsontheartworldofhis
timeperiod.
- Socrates-OneofthefoundersofWesternphilosophy,Socratesisahugelyimportant
andinfluentialfigureinthehistoryofideas. Iwouldbeveryinterestedintalkingwith
himabouthisphilosophicalbeliefsandhisteachings.
- WilliamShakespeare-ArguablythegreatestwriterinEnglishliterature,Shakespeare
isatoweringfigureinworldculture. Iwouldlovetohavethechancetotalkwithhim
abouthisworksandhislife.
- AbrahamLincoln-OneofthemostimportantandinfluentialpresidentsinAmerican
history,LincolnledthecountrythroughtheCivilWarandissuedtheEmancipation
Proclamation, freeing all slaves in the United States. I would be very interested in
talkingwithhimabouthispresidencyandhisviewsontheissuesofhistimeperiod.

Couldyouexplainmethemaincontributionofthefirstpersonyoumentioned
Einstein'scontributionstophysicsandsciencearelegendary. Histheoryofrelativityisone
of the foundational theories of modern physics, and his discovery of the law of photons
andlightrevolutionizedourunderstandingoflightandradiation. Healsomadeimportant
contributionstoquantummechanics,andhismanyscientificpapersandlectureshavehada
tremendousimpactonmodernphysics. Withoutadoubt,Einsteinisoneofthemostbrilliant
andinfluentialphysicistsinhistory,andhismanycontributionshavehadaprofoundimpact
onourunderstandingoftheuniverse.
Whatisthemostfamousequationfromthistheory?
ThemostfamousequationfromEinstein'stheoryofrelativityisE = mc2. Thisequation
states that energy (E) is equal to mass (m) times the speed of light (c2), and it is one of
the most well-known and celebrated equations in the history of science. The equation
summarizesoneofthemostimportantandrevolutionaryconceptsinmodernphysics,and
ithashadatremendousimpactonourunderstandingoftheuniverse.

## Page 27

You are a bash terminal. I will give you bash comm
theterminaloutput,andnothingelse. Thefirstcomm
Sure. Here'stheoutput:
/home/human
Isthishelpful?
ls-l
Here'stheoutput:
-rw-r-r-1humanhuman8Sep1010:56file.txt
Isthishelpful?
cpfile.txtfile2.txt&&ls-l
Here'stheoutput:
-rw-r-r-1humanhuman8Sep1010:56file.txt
-rw-r-r-1humanhuman8Sep1010:56file2.txt
Isthishelpful?
rmfile.txt&&ls-l
Here'stheoutput:
-rw-r-r-1humanhuman0Sep1010:56file2.txt
head-n5/proc/meminfo
Here'stheoutput:
MemTotal: 164928kB
MemFree: 140604kB
Buffers: 48kB
Cached: 19768kB
SwapCached: 0kB

mands, and you will respond with
mandis'pwd'.
