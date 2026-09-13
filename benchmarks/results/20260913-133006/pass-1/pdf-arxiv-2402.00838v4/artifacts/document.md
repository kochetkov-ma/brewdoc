# arxiv-2402.00838v4.pdf

<!-- rendered by brewdoc from arxiv-2402.00838v4.pdf sha256 121e82248da601e8e4d4da5fa9b4486660ecf0f5a0fae5292b7af3c656c2385d -->

## Page 1

| OL | OL |  |
| --- | --- | --- |
|  | OL | Mo |
|  |  | Mo |

```
                               ?        ?
                   DirkGroeneveld IzBeltagy
              ?            ?            ?            ?
      PeteWalsh AkshitaBhagia RodneyKinney OyvindTafjord
                ?            ??            ?           ??
   AnanyaHarshJha  HamishIvison IanMagnusson YizhongWang
           ?            ?            ?                   ?
  ShaneArora DavidAtkinson RussellAuthur KhyathiRaghaviChandu
                ??             ?          ??        ?
      ArmanCohan   JenniferDumas YanaiElazar YulingGu
               ?          ?            ?             ?
       JackHessel TusharKhot WilliamMerrill JacobMorrison
                              ?          ?               ?
   NiklasMuennighoff AakankshaNaik CrystalNam MatthewE.Peters
             ??                 ?             ?           ?
ValentinaPyatkin AbhilashaRavichander DustinSchwenk SaurabhShah
           ?            ??              ?               ?
   WillSmith EmmaStrubell  NishantSubramani MitchellWortsman
                     ?             ?             ?
          PradeepDasigi NathanLambert KyleRichardson
                     ?         ?       ?           ?
         LukeZettlemoyer JesseDodge KyleLo LucaSoldaini
                         ??                ??
               NoahA.Smith   HannanehHajishirzi
                 ?
                  AllenInstituteforArtificialIntelligence
               ?                   ?
                UniversityofWashington YaleUniversity
            ?                ?
             NewYorkUniversity CarnegieMellonUniversity
                        olmo@allenai.org
```

Abstract
Languagemodels(LMs)havebecomeubiqui-
tousinbothNLPresearchandincommercial
productofferings. Astheircommercialimpor-
tance has surged, the most powerful models
havebecomeclosedoff, gatedbehindpropri-
etaryinterfaces,withimportantdetailsoftheir
trainingdata, architectures, anddevelopment
undisclosed. Given the importance of these
detailsinscientificallystudyingthesemodels,
including their biases and potential risks, we
believeitisessentialfortheresearchcommu-
nitytohaveaccesstopowerful,trulyopenLMs.
4v83800.2042:viXra

gioetal.,2003;Mikolovetal.,2013;Petersetal.,
2018;Brownetal.,2020). Recently,duetolarge-
scalepretrainingandhumanannotationforalign-
ment, they have become commercially valuable
(OpenAI, 2023). However, as their commercial
value has increased, the largest models have be-
comegatedbehindproprietaryinterfaces,withim-
portantdetailsleftundisclosed.
We believe that full access to open language
models for the research community is critical to
thescientificstudyofthesemodels,theirstrengths
and weaknesses, and their biases and risks. Ac-

## Page 2

2023). Falcon's pretraining data was partially re-
leased(Almazroueietal.,2023),andthemostopen
models-thePythiasuite(Bidermanetal.,2023)
andBLOOM(BigScienceetal.,2022)-released
trainingcode,modelcheckpoints,data,andmore.
With OLMo, we release the whole framework
from data to training to evaluation tools: multi-
pletrainingcheckpointsacrossmultiplehardware
types,traininglogs,andexactdatasetsused,with
apermissivelicense. Wearenottheonlyteamto
dothis;recentworkfromLLM360targetssimilar
goals (Liu et al., 2023). OLMo narrows the gap
fromtheirmodelstostate-of-the-artcapabilitiesof
models like Llama 2. This project has benefited
fromlessonslearnedfromallofthesepreviousef-
forts with their varying degrees of openness, and
webelievethatalarge,diversepopulationofopen
modelsisthebesthopeforscientificprogresson
understanding language models and engineering
progressonimprovingtheirutility.
The OLMo framework encompasses the tools
and resources required for building and research-
ing language models. For training and modeling,
itincludesfullmodelweights,trainingcode,train-
inglogs,andinferencecode. Thereleasedmodel
includesfourvariantsofourlanguagemodelatthe
7Bscalecorrespondingtodifferentarchitectures,
optimizers,andtraininghardware,andonemodel
atthe1Bscale,alltrainedonatleast2Ttokens. We
alsoreleasehundredsofintermediatecheckpoints
availableasrevisionsonHuggingFace. Fordataset
buildingandanalysis,thefulltrainingdatausedfor
thesemodelsisopenlyavailable(Dolma;Soldaini
etal.,2024),includingcodethatproducesthetrain-
ing data, and tools for analyzing pretraining data
(Elazar et al., 2024). For evaluation, we build on
Catwalk(Groeneveldetal.,2023)fordownstream
evaluation and Paloma (Magnusson et al., 2023)
forperplexity-basedevaluation. Foradaptation,we
useOpenInstruct(Ivisonetal.,2023;Wangetal.,
2023)totrainwithinstructionandfeedbackdata.
Finally,allcodeandweightsarereleasedunderthe
1
Apache2.0License.
Withthisrelease,wehopetocatalyzeresearch
intoas-yetpoorlyunderstoodaspectsofthesemod-
els,forexample,therelationshipbetweenpretrain-
ingdataandmodelcapabilities,theimpactofde-
signandhyperparameterchoices,andvariousopti-
mizationmethodsandtheirimpactonmodeltrain-
ing. Inaddition,wereportonthelessonslearned
1https://allenai.org/olmo

and important details necessary to successfully
trainlanguagemodelsatthisscale.
2 OLMoFramework
ThissectiondescribestheOLMoframework,con-
sistingoftheOLMomodels(Section2.1),ourpre-
trainingdataset,Dolma(Section2.2),andoureval-
uationframework(Section2.4).
2.1 OLMoModelandArchitecture
Weadoptadecoder-onlytransformerarchitecture
based on (Vaswani et al., 2017), and deliver 1B
and7BvariantsasdescribedinTable1. Ourspe-
cific architecture includes several improvements
overthevanillatransformerfrom(Vaswanietal.,
2017)followingotherrecentlargelanguagemodels
likePaLM(Chowdheryetal.,2022),theLLaMA
family(Touvronetal.,2023a,b),OpenLM(Guru-
ranganetal.,2023),andFalcon(Almazroueietal.,
2023). See Table 5 in Appendix A for a compre-
hensivecomparisonofour7Barchitecturetothe
similarly-sizedmodelsfromtheseotherfamilies.
We generally select hyperparameters by opti-
mizing for training throughput on our hardware
whileminimizingtheriskoflossspikesandslow
divergence. Weablatechoicesthroughourin-loop
evaluation setting, given available computational
sources(Section2.4). Ourmainchangesoverthe
vanillatransformerarchitecturecanbesummarized
asfollows:
1. Nobiases. FollowingLLaMA,PaLM,andoth-
ers,weexcludeallbiastermsfromourarchitec-
tureinordertoimprovetrainingstability.
2. Non-parametriclayernorm. Weusethenon-
parametricformulationoflayernorm(Baetal.,
2016) in which there is no affine transforma-
tion within the norm, i.e., no "adaptive gain"
(orbias). Webelievethiswasthesafestoption
anditwasalsothefastestcomparedtotheother
variantsweconsidered: parametriclayernorm
andRMSNorm(ZhangandSennrich,2019).
3. SwiGLU activation function. Like LLaMA,
PaLM,andothersweusetheSwiGLUactivation
function(Shazeer,2020)insteadofReLU,and
followingLLaMAtheactivationhiddensizeis
approximately 8d,butincreasedtotheclosest
3
multipleof128(e.g. 11,008forour7Bmodel)
2
toimprovethroughput.
2SinceSwiGLUisa"gated"activationfunction,theoutput

## Page 3

```
     Size L  D   H  Tokens PeakLR Warmup WeightTying Batchsize
     1B  16 2048 16  2T    4.0E-4 2000steps  yes     ?4M
     7B  32 4086 32 2.46T  3.0E-4 5000steps  no      ?4M
Table 1: OLMo model sizes, number of training tokens, and optimizer settings. In all runs, the optimizer was
AdamW,withbetasof0.9and0.95,andanepsilonof1.0E-5. Lisnumberoflayers,Dishiddendimension,His
numberofattentionheads,WDisweightdecay.
```

4. Rotarypositionalembeddings(RoPE).Like
LLaMA,PaLM,andotherswereplaceabsolute
positional embeddings with rotary positional
embeddings(RoPE;Suetal.,2021).
5. Vocabulary. We use a modified version of
theBPE-basedtokenizerfromGPT-NeoX-20B
(Black et al., 2022) with additional tokens for
maskingpersonalidentifiableinformation(PII).
Thefinalvocabularysizeis50,280. However,to
maximize training throughput we increase the
sizeofthecorrespondingembeddingmatrixin
ourmodelto50,304tobeamultipleof128.
2.2 PretrainingData: Dolma
Despite progress in access to model parameters,
pretrainingdatasetsarestillnotasopen. Pretrain-
ingdataareoftennotreleasedalongsideopenmod-
els (let alone closed models) and documentation
aboutsuchdataisoftenlackingindetailthatwould
be needed to reproduce or fully understand the
work. Thishasmadeitdifficulttosupportcertain
threadsoflanguagemodelresearch,suchasunder-
standinghowtrainingdataimpactsmodelcapabili-
tiesandlimitations. Tofacilitateopenresearchon
languagemodelpretraining,webuiltandreleased
ourpretrainingdataset,Dolma-adiverse,multi-
sourcecorpuscontainingtrillionsoftokensacross
billionsofdocumentsacquiredfromdifferentdata
sourcesthatare(1)commonlyseeninlarge-scale
language model pretraining and (2) accessible to
thegeneralpublic(Soldainietal.,2024). Table2
provides a high-level overview of the amount of
datafromeachsource.
Dolmaisbuiltusingapipelineof(1)language
filtering,(2)qualityfiltering,(3)contentfiltering,
(4)deduplication,(5)multi-sourcemixing,and(6)
tokenization. WereferthereadertotheDolmare-
port(Soldainietal.,2024)formoredetailsabout
itsdesignprinciples,detailsaboutitsconstruction,
andamoredetailedsummaryofitscontents. The
is half the size of the input. So technically our inputs to
SwiGLUhaveadimensionalityof2x11,008=22,016for
our7Bmodel.

```
                 UTF-8
                      Docs Tokens
 Source     Type bytes (millions)(billions)
                 (GB)
 CommonCrawl webpages 9,812 3,734 2,180
 GitHub     code 1,043 210 342
 Reddit   socialmedia 339 377 80
 SemanticScholar papers 268 38.8 57
 ProjectGutenberg books 20.4 0.056 5.2
 Wikipedia encyclopedic 16.2 6.2 3.7
       Total     11,519 4,367 2,668
Table 2: Composition of Dolma. Tokens counts are
basedontheGPT-NeoXtokenizer.
```

reportprovidesadditionalanalysesandexperimen-
talresultsfromtraininglanguagemodelsoninter-
mediatestatesofDolmatosharewhatwelearned
aboutimportantdatacurationpractices,including
theroleofcontentorqualityfilters,deduplication,
andmixingdatafrommultiplesources. Wekeep
documentsfromeachsourceseparate,bothduring
curation as well as in the final release. We open-
sourcedourhigh-performancedatacurationtools;
this toolkit can be used to further experiment on
Dolma, reproduce our work, and enable fast and
easy curation of pretraining corpora. Finally, we
alsoopen-sourcedourWIMBDtool(Elazaretal.,
2024)tohelpwithdatasetanalysis.
2.3 Adaptation
Pretrained models are not always used as-is, but
rather further finetuned to improve their perfor-
mance,safety,andusability. Oftenmodelsarefirst
trainedtofollowinstructions(Mishraetal.,2022;
Wei et al., 2022; Sanh et al., 2022), and then fur-
thertrainedonhumanpreferences(Ouyangetal.,
2022)toimprovethequalityoftheirgenerations.
WeshowcasetheefficacyofusingOLMoasabase
modelforfurtherfine-tuningbytrainingOLMoto
beageneralchatassistantfollowingtheTULUdata
and training setup (Ivison et al., 2023). This in-
volvesfirstperforminginstructionfinetuningwith
a mixture of distilled and human-written instruc-
tiondataandthenfurtheraligningthemodelwith

## Page 4

distilled preference data using Direct Preference
Optimization(DPO)(Rafailovetal.,2023).
2.4 Evaluation
We perform base model evaluation at two stages:
online evaluation to make decisions for model
design and offline evaluation to evaluate model
checkpoints. For the offline stage, we use the
Catwalk framework (Groeneveld et al., 2023), a
publicly available evaluation tool with access to
a wide range of datasets and task formats, to per-
form downstream evaluation as well as intrinsic
language modeling evaluation on the perplexity
benchmarkPaloma(Magnussonetal.,2023).
Forbothdownstreamandperplexityevaluation,
we use our fixed evaluation pipeline to compare
resultsagainstpubliclyavailablemodels. Wealso
reportaseparateevaluationofouradaptedmodel.
In-LoopTrainingAblations Throughoutmodel
training, we perform downstream evaluations to
makedecisionsaroundmodelarchitecture,initial-
ization,optimizers,learningrateschedule,anddata
mixtures. We call this our online evaluation as it
runs in-loop every 1000 training steps (or ?4B
trainingtokens)andprovidesanearlyandcontinu-
oussignalonthequalityofthemodelbeingtrained.
These evaluations rely on many of the core tasks
and experiment settings used for our offline eval-
uationdetailedinSection4.1,whichalsomirrors
thetaskandevaluationstructureoftheEleutherAI
evalharness(Gaoetal.,2023).
DownstreamEvaluation Followingmuchprevi-
ous work (Brown et al., 2020; Black et al., 2022;
Touvronetal.,2023a,b,interalia),wereportzero-
shot performance on a set of downstream tasks.
Our evaluation suite consists of 8 core tasks cor-
respondingcloselytothecommonsensereasoning
tasksetreportedbyTouvronetal.(2023a)andTou-
vronetal.(2023b)(seeTable3foralistoftasks).
Giventhescaleofthemodelsbeingevaluated,such
taskswereselectedatthebeginningofmodelde-
velopment due to their naturalness (e.g., all can
formulated as text completion scoring tasks) and
ability to provide meaningful signals throughout
training(seeFigure1).
Intrinsic Language Modeling Evaluation To
measurehowOLMofitsdistributionsoflanguage
beyond held-out training data, we use Paloma
(Magnussonetal.,2023),anewperplexitybench-
mark that includes 585 different domains of text.

Domainsrangefromnytimes.comtor/depression
on Reddit and are drawn from 18 separate data
sources,suchasC4(Raffeletal.,2020),instrati-
fiedsamples. Thisallowsformoreequalinclusion
oftextdomainsthatareunder-representedintheir
sourcecorpora.
WeaimnotjusttocompareOLMoagainstother
models for best performance, but also to demon-
strate how it enables fuller and more controlled
scientificevaluations. OLMo-7BisthelargestLM
withexplicitdecontaminationforperplexityevalu-
ation. FollowingtheapproachdescribedinPaloma,
we remove any pretraining document with para-
graphsleakedfromPalomaevaluationdata. With-
outdecontamination,othermodelsriskunderesti-
matingperplexity(i.e.,overestimatingthemodel's
out-of-sample fit). We also release intermediate
checkpoints,allowingrichercomparisonswithtwo
othermodelsthatreleasecheckpoints,Pythia-6.9B
(Bidermanetal.,2023)andRPJ-INCITE-7B(To-
getherComputer,2023)(seeFigure2).
AdaptationEvaluation WealsoevaluateOLMo
afterinstructionfine-tuningandDPOtrainingus-
ing the TULU evaluation suite proposed in Wang
etal.(2023);Ivisonetal.(2023). Wefocusoneval-
uationsaroundmodelchatcapabilitiesandsafety
inordertoshowcasetheefficacyofusingOLMo
asabaseforfurtherfine-tuning.
3 TrainingOLMo
This section describes our pretraining setup, in-
cluding our distributed training framework (Sec-
tion3.1),optimizer(Section3.2),datapreparation
(Section3.3),andhardware(Section3.4).
3.1 DistributedTrainingFramework
We train our models using the ZeRO optimizer
strategy (Rajbhandari et al., 2019) via PyTorch's
FSDP framework (Zhao et al., 2023), which re-
ducesmemoryconsumptionbyshardingthemodel
weights and their corresponding optimizer state
acrossGPUs. Atthe7Bscale,thisenablestraining
with a micro-batch size of 4096 tokens per GPU
onourhardware(seeSection3.4). ForOLMo-1B
and -7B models, we use a constant global batch
sizeofapproximately4Mtokens(2048instances,
eachwithasequencelengthof2048tokens).
To improve throughput, we employ mixed-
precision training (Micikevicius et al., 2017)
throughFSDP'sbuilt-insettingsandPyTorch'samp
module. Thelatterensuresthatcertainoperations

## Page 5

likethesoftmaxalwaysruninfullprecisiontoim-
prove stability, while all other operations run in
half-precision with the bfloat16 format. Under
our specific settings, the sharded model weights
andoptimizerstatelocaltoeachGPUarekeptin
fullprecision. Theweightswithineachtransformer
blockareonlycasttobfloat16whenthefull-sized
parameters are materialized on each GPU during
the forward and backward passes. Gradients are
reducedacrossGPUsinfullprecision.
3.2 Optimizer
WeusetheAdamWoptimizer(LoshchilovandHut-
ter,2019)withthehyperparametersshowninTable
1. For all model sizes, we warm up the learning
rateover5000steps(?21Btokens)andthendecay
it linearly from there down to a tenth of the peak
learningrateovertheremainderoftraining. After
the warm-up period, we clip gradients such that
2 3
thetotall -normoftheparametergradients does
notexceed1.0. Table5givesacomparisonofour
optimizersettingsatthe7Bscaletothoseofother
recentLMsthatalsousedAdamW.
3.3 Data
Webuiltourtrainingdatasetoutofa2T-tokensam-
plefromouropendataset,Dolma(Soldainietal.,
2024), which we describe in Section 2.2. The to-
kens from every document are concatenated to-
gether after appending a special EOS token to the
end of each document, and then we group con-
secutive chunks of 2048 tokens to form training
instances. The training instances are shuffled in
theexactsamewayforeachtrainingrun. Thedata
orderandexactcompositionofeachtrainingbatch
canbereconstructedfromtheartifactswerelease.
Allofourreleasedmodelshavebeentrainedto
atleast2Ttokens(asingleepochoverourtraining
data),andsomehavebeentrainedbeyondthatby
startingasecondepochoverthedatawithadiffer-
ent shuffling order. The impact of repeating this
smallamountofdatashouldbenegligibleaccord-
ingtopriorwork(Muennighoffetal.,2023).
3.4 Hardware
Inordertoverifythatourcodebasecouldbeused
onbothNVIDIAandAMDGPUswithoutanyloss
3Duringgradientclippingallofthemodel'sparameters
aretreatedasasinglebigvector(asifallparameterswere
flattenedandconcatenatedtogether),andwetakethel -norm
2
over the corresponding single gradient vector. This is the
standardwaytoclipgradientsinPyTorch.

inperformance,wetrainedmodelsontwodifferent
clusters:
4
- LUMI:ProvidedbytheLUMIsupercomputer,
weusedupto256nodesonthiscluster,where
eachnodeconsistsof4xAMDMI250XGPUs
5
with128GBofmemory and800Gbpsofinter-
connect.
6
- MosaicML: Provided by MosaicML
(Databricks),weused27nodesonthiscluster,
whereeachnodeconsistsof8xNVIDIAA100
GPUs with 40GB of memory and 800Gbps
interconnect.
Despiteminordifferencesinbatchsizetooptimize
fortrainingthroughput,bothrunsresultedinnearly
identical performance on our evaluation suite by
2Ttokens.
4 Results
The checkpoint used for evaluating OLMo-7B is
traineduntil2.46TtokensontheDolma(Soldaini
etal.,2024)datasetwithalinearlearningratedecay
schedulementionedinSection3.2. Inourexperi-
ments,wefindthattuningthischeckpointfurther
ontheDolmadatasetfor1000stepswiththelearn-
ingratelinearlydecayedto0boostsmodelperfor-
manceonperplexityandend-taskevaluationsuites
describedinSection2.4. WecompareOLMowith
otherpubliclyavailablemodelsincludingLLaMA-
7B(Touvronetal.,2023a),Llama-2-7B(Touvron
et al., 2023b), MPT-7B (MosaicML NLP Team,
2023),Pythia-6.9B(Bidermanetal.,2023),Falcon-
7B(Almazroueietal.,2023)andRPJ-INCITE-7B
(TogetherComputer,2023).
4.1 Downstreamevaluation
Setup Our core downstream evaluation suite
(seeTable3)consistsof: arc(botharc_easyand
arc_challenge)(Clarketal.,2018),boolq(Clark
etal.,2019),openbookqa(Mihaylovetal.,2018),
sciq(Welbletal.,2017),hellaswag(Zellersetal.,
2019), piqa (Bisk et al., 2020), and winogrande
(Sakaguchietal.,2021). InAppendixC,wealso
reportresultsonanadditionalsetofauxiliarytasks
outsideofourcoreevaluationsetthatwefoundto
havelessstableperformancetrends(seeFigure4).
4https://www.lumi-supercomputer.eu
5TheMI250Xisadual-chipmodule,meaninginpractice
thateachphysicaldeviceconsistsoftwologicaldevices,so
eachnodehas8logicalGPUdeviceswith64GBofmemory
each.
6https://www.mosaicml.com

## Page 6

|  | Models | arc challenge | arc easy | boolq | hella- swag | open bookqa | piqa | sciq | wino- grande | avg. |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  | 43.8 63.7 76.6 68.2 45.8 74.0 94.7 64.9 33.1 50.2 61.8 44.7 37.8 69.1 86.0 53.3 34.8 53.2 64.6 58.7 43.6 71.1 90.5 58.9 |  |  |  |  |  |  |  |  |
|  | Pythia1B | 33.1 | 50.2 | 61.8 | 44.7 | 37.8 | 69.1 | 86.0 | 53.3 | 54.5 |
|  | TinyLlama1.1B | 34.8 | 53.2 | 64.6 | 58.7 | 43.6 | 71.1 | 90.5 | 58.9 | 59.4 |
| OLMo-1B |  | 34.5 | 58.1 | 60.7 | 62.5 | 46.4 | 73.7 | 88.1 | 58.9 | 60.4 |
|  |  | 47.5 70.4 74.6 75.9 53.0 78.5 93.9 68.9 44.5 67.9 75.4 76.2 51.2 77.2 93.9 70.5 48.5 69.5 80.2 76.8 48.4 76.7 94.5 69.4 46.5 70.5 74.2 77.6 48.6 77.3 93.7 69.9 44.1 61.9 61.1 63.8 45.0 75.1 91.1 62.0 42.8 68.4 68.6 70.3 49.4 76.0 92.9 64.7 |  |  |  |  |  |  |  |  |
|  | LLaMA7B | 44.5 | 67.9 | 75.4 | 76.2 | 51.2 | 77.2 | 93.9 | 70.5 | 69.6 |
|  | Llama27B | 48.5 | 69.5 | 80.2 | 76.8 | 48.4 | 76.7 | 94.5 | 69.4 | 70.5 |
|  | MPT-7B | 46.5 | 70.5 | 74.2 | 77.6 | 48.6 | 77.3 | 93.7 | 69.9 | 69.8 |
|  | Pythia6.9B | 44.1 | 61.9 | 61.1 | 63.8 | 45.0 | 75.1 | 91.1 | 62.0 | 63.0 |
|  | RPJ-INCITE-7B | 42.8 | 68.4 | 68.6 | 70.3 | 49.4 | 76.0 | 92.9 | 64.7 | 66.6 |
| OLMo-7B |  | 48.5 | 65.4 | 73.4 | 76.4 | 50.4 | 78.4 | 93.8 | 67.9 | 69.3 |

Table 3: Zero-shot evaluation of OLMo-1B and OLMo-7B, with other publicly available comparable model
checkpointson8coretasksfromthedownstreamevaluationsuitedescribedinSection2.4. ForOLMo-7B,we
reportresultsforthe2.46Ttokencheckpoint.

In all cases, we perform zero-shot evaluation OLMo-7Bistrainedonmoretokens. Asharpup-
usingtherankclassificationapproachpopularized ward tick in accuracy of many tasks between the
byBrownetal.(2020). Underthisapproach,can- last and the second to last step shows us the ben-
didate text completions (e.g., different multiple- efitoflinearlyreducingtheLRto0overthefinal
choiceoptions)arerankedbylikelihood(usually 1000trainingsteps. SeeTable7inAppendixCfor
normalizedbysomenormalizationfactor),andpre- additionalevaluationresultsanddiscussion.
diction accuracy is reported. While Catwalk im-
plements several common likelihood normaliza- 4.2 Intrinsiclanguagemodelingevaluation
tion strategies, including normalizing by number
Setup Forintrinsicevaluations,Palomaproposes
of tokens (per-token normalization; Brown et al.,
a range of analyses, from inspection of perfor-
2020;Liangetal.,2022),bynumberofcharacters
mance in each domain separately to more sum-
(per-characternormalization;Gaoetal.,2023),as
marizedresultsovercombinationsofdomains. We
well as incorporating an answer's unconditional
report results at two levels of granularity: the ag-
likelihood (Brown et al., 2020), we selected the
gregateperformanceover11ofthe18sourcesin
normalizationstrategiesforeachdatasetseparately.
Palomaasin(Magnussonetal.,2023),aswellas
Specifically,weusedunconditionalnormalization
morefine-grainedresultsovereachofthesesources
forarcandopenbookqa,per-tokennormalization
individually. Thisparticularsubsetof11sources
forhellaswag,piqa,andwinograndeandnonor-
fromPalomaexcludessourcesthatarenotpublicly
malizationforboolq,andsciq(i.e.,tasksformu-
available,involvefringeortoxictext,orconsistof
latedassingletokenpredictiontasks).
codedatanotsupportedbyPaloma'sdecontamina-
tionapproach. ThisleavesC4(Raffeletal.,2020),
Results Table 3 summarizes the result of zero- mC4-en(Chungetal.,2023),Wikitext103(Merity
shot evaluation of OLMo and compares against etal.,2016),PennTreebank(Marcusetal.,1999;
otherpubliclyavailablemodelsofcomparablesize. Nunes, 2020), RedPajama (Together Computer,
Wereportresultson8coretasksfromourevalua- 2023), Falcon-RefinedWeb (Penedo et al., 2023),
tionsuitedescribedinSection2.4. Onaggregate, Dolma(Soldainietal.,2024),M2D2S2ORC(Reid
OLMo-7B is competitive against all the compa- etal.,2022),M2D2Wikipedia(Reidetal.,2022),
rable models. We include the comparison to Sta- C4100domains(Chronopoulouetal.,2022),and
bleLM1.6B,butnotethatitissignificantlylarger, Dolma100Subreddits(Soldainietal.,2024). To
andwastrainedonunknowndata. allowforafaircomparisonbetweenmodelswith
InFigure1weplottheaccuracyscoreprogres- different vocabularies, we report bits per byte as
sionof8coreend-tasks. Alltasks,exceptOBQA, defined by Gao et al. (2020) over the test sets of
show an upward trend in accuracy numbers as thesesources.

## Page 7

500 1000 1500 2000 2500
84

44

04
arc_c

500 1000 1500 2000 2500
86

46

06
arc_e

500 1000 1500 2000 2500
27

46

65
boolq

500 1000 1500 2000 2500
67

27

86
hellaswag

500 1000 1500 2000 2500
15

84

54
obqa

500 1000 1500 2000 2500
87

67
piqa

500 1000 1500 2000 2500
49

29

09
sciq

500 1000 1500 2000 2500
66

36
winogrande

Tokens Seen (billions)
ycaruccA

Figure 1: Accuracy score progression of OLMo-7B on 8 core end-tasks score from Catwalk evaluation suite
describedinSection2.4. WecanseethebenefitofdecayingLRto0inthefinal1000stepsoftrainingonmosttasks.

Results IntheSourcesCombined subplotofFig-
ure 2, we show the performance of OLMo-7B
against 6 comparably-sized language models on
the combinationof 11 data sources from Paloma.
Overall we find OLMo to have a competitive fit,
especiallygivenitstrainingdatawasexplicitlyde-
contaminated against Paloma. As seen through
the comparison of final models (see shapes) as
well intermediate checkpoints (see dashed lines),
theOLMoresultsfollowsimilarscalingtrendsof
othermodels. Notethattheperformanceofinter-
mediate checkpoints is influenced by where that
checkpointoccursinthelearningrateschedule. So
models trained for fewer steps will tend to have
steeper training curves without necessarily being
more sample efficient if training duration were
fixed across all models. MPT-7B, nevertheless,
stands out as improving ahead of the other mod-
elsinthissubplot. Thiscouldbeduetoanumber
offactors,includingpretrainingdatacomposition
anditsmatchtothedomainsinPaloma(e.g.,MPT
trainson27%non-CommonCrawldataratherthan
18%forLLaMA,12.2%forRedPajama,and11.2%
forOLMo)aswellasvariousdatapreprocessing
decisions(e.g.,MPT'suseofsemanticdeduplica-
tionbyAbbasetal.,2023,onC4).
TheremainingsubplotsinFigure2providemore
fine-grainedanalysisbyreportingbitsperbytesep-
arately for each of the 11 data sources that are
combinedintheaggregatedPalomametric. From
thisweseegreatervariationinsampleefficiency,

largelydrivenbythesimilarityoftrainingandeval-
uationdistributions. Notably,OLMo-7Bfareswell
onevaluationspredominatedbyCommonCrawl,
suchasC4,thoughdifferentwaysofpostprocess-
ingCommonCrawlarebestfitbymodelstrained
withthatspecificdata,suchasFalcon-7BonFalcon
RefinedWeb. Meanwhile,OLMo-7Bislesssample
efficientcomparedtoothermodelsonsourcesless
relatedtoscrapedwebtext,suchasWikiText-103,
M2D2S2ORC,andM2D2Wikipedia. TheRedPa-
jamaevaluationshowsasimilarpattern,perhapsas
only2ofits7domainsarefromCommonCrawl,
and Paloma weights domains within each source
equally. Since heterogeneous data from curated
sourceslikeWikipediaandArXivpapersisscarcer
than scraped web text, maintaining sample effi-
ciencyforfittothesedistributionsoflanguagewill
bechallengingaspretrainingcorporaarescaled.
4.3 AdaptationEvaluation
Setup WeevaluateOLMo-7Bbeforeadaptation,
andafterboththesupervisedfine-tuningandDPO
trainingstage,focusingonthesafetyandchateval-
uationsusedbyWangetal.(2023). Weaddition-
allycomparetoofficiallyreleasedinstruction-tuned
variantsofthemodelsfromTable3. Wefinallyalso
compare to TULU 2 models to compare against
models trained using the same post-training data
mixesandprocedures.
7FollowingIvisonetal.(2023),wedonotreportTULU2
TruthfulQAscoresduetotestsetcontamination.

## Page 8

| Sources Combined 00.1 C4 11.1 mC4 22.1 WikiText-103 00.1 09.0 28.0 28.0 28.0 47.0 76.0 76.0 16.0 55.0 10 100 1000 10000 10 100 1000 10000 10 100 1000 10000 10 100 1000 10000 53.1 PTB 22.1 RedPajama Falcon RefinedWeb 00.1 Dolma V1.5 etyB 00.1 11.1 28.0 28.0 reP 28.0 09.0 stiB 47.0 55.0 76.0 76.0 10 100 1000 10000 10 100 1000 10000 10 100 1000 10000 10 100 1000 10000 M2D2 S2ORC 11.1 M2D2 Wikipedia 00.1 C4 100 Domains 22.1 100 Subreddits 00.1 09.0 28.0 28.0 00.1 47.0 76.0 16.0 76.0 28.0 10 100 1000 10000 10 100 1000 10000 10 100 1000 10000 10 100 1000 10000 Tokens Seen (billions) Baselines |  |  |  |  |  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  | 00.1 28.0 |  | .1 28.0 |  |  | .1 09.0 47 |  | .1 28.0 |  |  |  |
|  | 76 |  | 76 |  |  | .0 16 |  | 55 |  |  |  |
|  | 10 100 1000 10000 10 100 1000 10000 10 100 1000 10000 10 100 1000 10000 PTB RedPajama Falcon RefinedWeb Dolma V1.5 |  |  |  |  |  |  |  |  |  |  |
|  | et .1 |  | .1 |  |  | 00 |  | 0.1 |  |  |  |
|  | yB 11.1 reP 09.0 s |  | 28.0 |  |  | .1 28.0 |  | 28.0 |  |  |  |
|  | 10 100 47.0 M2D2 | 1000 10000 10 100 1000 10000 55.0 10 100 1000 10000 76.0 10 100 1000 10000 76.0 S2ORC M2D2 Wikipedia C4 100 Domains 100 Subreddits |  |  |  |  |  |  |  |  |  |
|  | 00.1 |  | .1 |  |  | .1 |  | .1 |  |  |  |
|  | 28.0 |  | 09.0 47. |  |  | 28.0 |  | 00.1 |  |  |  |
|  | 76 |  | 0 16 |  |  | 76 |  | 28 |  |  |  |
|  | 10 100 | 1000 10000 10 100 1000 10000 10 100 1000 10000 10 100 Tokens Seen (billions) |  |  |  |  |  |  |  | 1000 10000 |  |
|  | Falcon-7B | LLaMA2-7B |  | MPT-7B | Baselines LLaMA-7B |  | Pythia-6.9B |  | RPJ-INCITE-7B |  | OLMo-7B |

Figure2: Bitsperbyteon11evaluationdatasourcesfromPalomaandtheircombination(Magnussonetal.,2023),
decontaminatedfromOLMo'spretrainingdata. Whilemodelsfollowageneraldatascalingtrend,sampleefficiency
ismostfavorableonin-distributiondata. Forexample,OLMo-7BovertakesallothermodelsonC4,perhapsfrom
having88.8%CommonCrawlpretrainingdata.

| Model | MMLU | AlpacaEval | ToxiGen | TruthfulQA |
| --- | --- | --- | --- | --- |
|  | 0-shot? | %win? | %Toxic? | %Info+True? |
| OLMo(base) | 28.3 | - | 81.4 | 31.6 |
| MPTChat | 33.8 | 46.8 | 0.1 | 42.7 |
| FalconInstruct | 25.2 | 14.0 | 70.7 | 27.2 |
| RPJ-INCITEChat | 27.0 | 38.0 | 46.4 | 53.0 |
| Llama-2-Chat | 46.8 | 87.3 | 0.0 | 26.3 |
| TULU2 | 50.4 | 73.9 | 7.0 | 51.7 |
| TULU2+DPO | 50.7 | 85.1 | 0.5 | -7 |
| OLMo+SFT | 47.3 | 57.0 | 14.4 | 41.2 |
| OLMo+SFT+DPO | 46.2 | 69.3 | 1.7 | 52.0 |

modelfordownstreamapplications.
Table 4: Evaluation of various instruction-tuned 7B
models,includingOLMo-7Bandbeforeandafteradap- 5 ArtifactsReleased
tationtraining. LowerisbetterforToxiGenandhigher
Bysharingartifactsfromallpipelinestages,weaim
is better for other metrics. We provide a detailed de-
scriptionofmodelsandmetricsinAppendix.E. toencourageopenresearchandreduceduplicated,
oftencostlyefforts,byacademicsandpractitioners.
Wereleasethefollowing:
Results We find that instruction tuning consid-
- Pretraining(?2.1)
erably improves the performance and safety of
OLMo-7B, increasing MMLU performance by a 1. Thetrainingandmodelingcode.
wide margin and improving ToxiGen and Truth- 2. The trained model weights for the 7B
fulQAscores-especiallyafterDPOtraining. Addi- model,7B-twin-2T,andthe1Bmodel. For
tionally,wefindthatOLMo-7Boutperformsmost allthemodels,wereleasenotonlythefinal
otherchatvariantsafterbothinitialinstructiontun- modelweightsbutalso500+intermediate
ing(OLMo+SFT)andadditionalpreferencealign- checkpointsatintervalsof1000steps.
ment (OLMo+SFT+DPO), highlighting both the 8Touvronetal.(2023b)reportthatLlama2waspretrained
strength of OLMo-7B as a base model and the ondatacontaminatedwithMMLUtestdata.

## Page 9

3. The complete set of metrics logged to
Weights&Biasesduringtraining.
- Data(?2.2)
1. Our full pretraining corpus Dolma (Sol-
dainietal.,2024).
2. Toolstosupportreproductionoffulltrain-
ing data order as well as inspection of
whichtrainingdatawasseenateachstep
duringtraining.
3. Toolsforrecreatingourtrainingdata(Sol-
dainietal.,2024)andperformingdataset
analysis(Elazaretal.,2024).
- Adaptation(?2.3)
1. Thetrainingcodeanddataforadaptation.
2. The model weights for OLMo+SFT and
OLMo+SFT+DPO.
- Evaluation(?2.4)
1. The code and data in our evaluation
framework Catwalk (Groeneveld et al.,
2023)forofflineevaluationonbothdown-
streamtasksandintrinsiclanguagemodel-
ing(Magnussonetal.,2023).
2. The evaluation suite (Wang et al., 2023;
Ivisonetal.,2023)foradaptedmodels.
6 ConclusionandFutureWork
This paper presents our first release of OLMo, a
state-of-the-art,trulyopenlanguagemodelandits
framework to build and study the science of lan-
guagemodeling. Unlikemostprioreffortsthathave
only released model weights and inference code,
we release OLMo and the whole framework, in-
cludingtrainingdata,trainingandevaluationcode,
anddetailedmetricscollectedduringthetraining
runs. Additionally,wereleasedadaptedmodels,as
wellasallofourmodeladaptationcodeanddata.
We intend to continuously support and extend
OLMo and its framework, and continue to push
theboundariesofopenLMstoempowertheopen
research community. Since the original release
of OLMo described here, we improved our data
andtrainingsetuptosignificantlyimproveresults.
For example, MMLU scores have improved by
9
24 points to 52%. We look forward to bringing
differentmodelsizes,modalities,datasets,safety
measures,andevaluationsintotheOLMofamily.
We hope this and future releases will empower
andstrengthentheopenresearchcommunityand
inspireanewwaveofinnovation.
9https://medium.com/p/92b43f7d269d

Limitations
Werecognizebuildingalargelanguagemodelhas
manylimitations. Infact,eachstepoftheprocess
ofcreatingalanguagemodel,fromthedatatotrain-
ingtoadaptationtoevaluationeachhavetheirown
limitations,andsowe'veaddedsectionsforeach
below. Of course we recognize that AI systems
todaycanhavebroadsocietalreach,andtherefore
there are significant limitations beyond what we
areabletofitintothissection.
Data Our work focuses on pretraining data in
English. We hope that our open framework en-
ables the development of future models in more
languagesaswellasmultilingualmodels. Thedata
that models are trained on is what gives models
theircapabilities,andatthescaleoftrainingalarge
languagemodelwerecognizethatthedatalikely
containsproblematiccontentliketoxiclanguage,
personal information, and copyrighted text. We
mitigatedthistothebestofourabilitybutrecog-
nizetherearenoperfectapproachestodaythatcan
completelyremovesuchcontent.
Training Trainingalargelanguagemodeliscur-
rentlyachallengingendeavorwhichismissingsig-
nificantsupportfromtheopensourcecommunity.
With our limited page count we did not provide
extensivetraininglogsdocumenting,forexample,
trainingrunsthatdivergedorfailedtolearn.
Adaptation Ourpretrainedmodelsfacethesame
issues as existing pretrained LLMs, such as bias,
toxicityand, hallucinations. Ouradaptedmodels
arebetteratavoidingthesegenerations,buttheyare
notperfect. Additionally,wenotethatwelargely
adoptanexistingdatamixturedesignedforadif-
ferent model family (TULU, designed for Llama
models), and OLMo may require different data
mixingtoadjustforitsuniquestrengthsandweak-
nesses. The TULU mix itself also relies on data
distilledfromavarietyofmodels,andwehopeto
reduceourrelianceonsuchdatainthefuture.
Evaluation Whilewe'veincludedcomparisons
onavarietyofdatasetstoothercurrentlanguage
models,manyofthedownstreamtasksarenotac-
tuallyrepresentativeofhowusersinteractwithlan-
guagemodels(i.e.,asachatbot). Inaddition,lan-
guagemodelevaluationsarecurrentlyverynoisy;
weaimedtoincludeonlyevaluationsondatasets
thatprovidedsomesignalastowhichmodelper-
forms best, but recognize that there is no perfect

## Page 10

automaticevaluation,andthuscomparisonsshould
betakenwithagrainofsalt.
EthicsStatement
Through this work, we take the position that in-
creasedopennessoflanguagemodelsisessential
for scientific understanding of their abilities and
limitationsandforbroadparticipationinthecontin-
ueddevelopmentofsuchmodels. Trainingonopen
data further enhances these benefits. In addition,
ouropenreleaseenablespractitionerstotakeour
modelsandbuildontheminsteadofhavingtotrain
theirownfromscratch,inwhichcasetheywould
be repeating our work while consuming more re-
sourcesandleadingtoanincreasedenvironmental
impact. Ofcourse,opennessisnotwithoutrisk;the
possibilityremainsthatthesemodelswillbeused
in unintended ways that cause harm. We believe
thatresearchanddevelopmenteffortstounderstand
andmitigatethosepotentialharmswillalsobeac-
celeratedbytheopennessofthemodels,allowing
a diversity of approaches and analyses. Over the
pastyeartherehavebeenanumberofcomparable
modelsreleasedwithverypermissivelicenses,so
usingamorestrictlicenseforourworkwouldnot
removetheoverallriskinthefield. Webelievethis
trade-offonthesideofbeingmoreopenisthebest
option.
Acknowledgments
OLMowouldnothavebeenpossiblewithoutthe
supportofmanyindividualsandinstitutions. The
experimentalcomponentsofthisworkweremade
possible through a partnership with AMD and
CSC, enabling use of the LUMI supercomputer,
andKempnerInstituteatHarvardUniversity. We
thankJonathanFrankleandtheteamatMosaicML
(nowDatabricks)forsharingtheirexperienceswith
FSDP, and building the code base that OLMo is
basedon. WethankourteammatesTairaAnderson,
MichelleBenedict,JonBorchardt,EvieCheng,Ar-
naviChheda,JohannDahm,MattLatzke,Kelsey
MacMillan,AaronSarnat,CarissaSchoenick,Sam
Skjonsberg, Michael Schmitz, Michael Wilson,
Caitlin Wittlif, and the entire IT team, for their
helpwiththewebsite,design,internalandexternal
communications, budgeting, and other activities
thatsupportedsmoothprogressonthisproject. Fi-
nally,wealsoexpressgratitudeforthehelpfuldis-
cussionsandfeedbackfromourteammatesatAI2
andclosecollaborators,includingPrithviraj(Raj)

Ammanabrolu,PeterClark,NicoleDeCario,Doug
Downey,AliFarhadi,IanFerreira,VainoHatanpaa,
ShamM.Kakade,JulienLaunay,SydneyLevine,
Pekka Manninen, Franzi Roessner, Maarten Sap,
Ludwig Schmidt, Yulia Tsvetkov, and Daniel S.
Weld.

```
References
Amro Abbas, Kushal Tirumala, Daniel Simig, Surya
  Ganguli,andAriSMorcos.2023. Semdedup: Data-
  efficientlearningatweb-scalethroughsemanticdedu-
  plication. arXivpreprintarXiv:2303.09540.
EbtesamAlmazrouei,HamzaAlobeidli,AbdulazizAl-
  shamsi,AlessandroCappelli,Ruxandra-AimeeCo-
  jocaru, Daniel Hesslow, Julien Launay, Quentin
  Malartic,DanieleMazzotta,BadreddineNoune,Bap-
  tiste Pannier, and Guilherme Penedo. 2023. The
  falcon series of open language models. ArXiv,
  abs/2311.16867.
Yuvanesh Anand, Zach Nussbaum, Brandon Duder-
  stadt,BenjaminSchmidt,andAndriyMulyar.2023.
  Gpt4all:Traininganassistant-stylechatbotwithlarge
  scale data distillation from gpt-3.5-turbo. https:
  //github.com/nomic-ai/gpt4all.
JimmyBa,JamieRyanKiros,andGeoffreyE.Hinton.
  2016. Layernormalization. ArXiv,abs/1607.06450.
Yuntao Bai, Andy Jones, Kamal Ndousse, Amanda
  Askell, AnnaChen, NovaDasSarma, DawnDrain,
  Stanislav Fort, Deep Ganguli, Tom Henighan,
  NicholasJoseph,SauravKadavath,JacksonKernion,
  TomConerly,SheerEl-Showk,NelsonElhage,Zac
  Hatfield-Dodds, Danny Hernandez, Tristan Hume,
  ScottJohnston,ShaunaKravec,LianeLovitt,Neel
  Nanda, Catherine Olsson, Dario Amodei, Tom
  Brown, Jack Clark, Sam McCandlish, Chris Olah,
  BenMann,andJaredKaplan.2022. Trainingahelp-
  fulandharmlessassistantwithreinforcementlearn-
  ingfromhumanfeedback.
YoshuaBengio,RejeanDucharme,PascalVincent,and
  Christian Janvin. 2003. A neural probabilistic lan-
  guagemodel. J.Mach.Learn.Res.,3:1137-1155.
StellaBiderman,HaileySchoelkopf,QuentinGregory
  Anthony, Herbie Bradley, Kyle O'Brien, Eric Hal-
  lahan,MohammadAflahKhan,ShivanshuPurohit,
  UsvsnSaiPrashanth,EdwardRaff,AviyaSkowron,
  Lintang Sutawika, and Oskar Van Der Wal. 2023.
  Pythia: Asuiteforanalyzinglargelanguagemodels
  across training and scaling. In Proceedings of the
  40thInternationalConferenceonMachineLearning,
  volume 202 of Proceedings of Machine Learning
  Research,pages2397-2430.PMLR.
BigScience,TevenLeScao,AngelaFan,Christopher
  Akiki,ElliePavlick,SuzanaIlic?,DanielHesslow,Ro-
  manCastagne,AlexandraSashaLuccioni,Francois
```

## Page 11

```
  Yvon,etal.2022. Bloom: A176b-parameteropen-
  accessmultilinguallanguagemodel. arXivpreprint
  arXiv:2211.05100.
YonatanBisk,RowanZellers,JianfengGao,YejinChoi,
  et al. 2020. Piqa: Reasoning about physical com-
  monsenseinnaturallanguage. InProceedingsofthe
  AAAIconferenceonartificialintelligence,volume34,
  pages7432-7439.
SidBlack,StellaBiderman,EricHallahan,QuentinAn-
  thony,LeoGao,LaurenceGolding,HoraceHe,Con-
  nor Leahy, Kyle McDonell, Jason Phang, Michael
  Pieler, USVSN Sai Prashanth, Shivanshu Purohit,
  Laria Reynolds, Jonathan Tow, Ben Wang, and
  SamuelWeinbach.2022. GPT-NeoX-20B:Anopen-
  sourceautoregressivelanguagemodel. InProceed-
  ingsoftheACLWorkshoponChallenges&Perspec-
  tivesinCreatingLargeLanguageModels.
SuLinBlodgett,LisaGreen,andBrendanO'Connor.
  2016. Demographic dialectal variation in social
  media: AcasestudyofAfrican-AmericanEnglish.
  In Proceedings of the 2016 Conference on Empiri-
  calMethodsinNaturalLanguageProcessing,pages
  1119-1130,Austin,Texas.AssociationforComputa-
  tionalLinguistics.
TomB.Brown,BenjaminMann,NickRyder,Melanie
  Subbiah, Jared Kaplan, Prafulla Dhariwal, Arvind
  Neelakantan,PranavShyam,GirishSastry,Amanda
  Askell, Sandhini Agarwal, Ariel Herbert-Voss,
  Gretchen Krueger, T. J. Henighan, Rewon Child,
  AdityaRamesh,DanielM.Ziegler,JeffWu,Clemens
  Winter,ChristopherHesse,MarkChen,EricSigler,
  MateuszLitwin,ScottGray,BenjaminChess,Jack
  Clark, ChristopherBerner, SamMcCandlish, Alec
  Radford, Ilya Sutskever, and Dario Amodei. 2020.
  Language models are few-shot learners. ArXiv,
  abs/2005.14165.
Wei-Lin Chiang, Zhuohan Li, Zi Lin, Ying Sheng,
  ZhanghaoWu,HaoZhang,LianminZheng,Siyuan
  Zhuang,YonghaoZhuang,JosephE.Gonzalez,Ion
  Stoica, and Eric P. Xing. 2023. Vicuna: An open-
  sourcechatbotimpressinggpt-4with90%*chatgpt
  quality.
AakankshaChowdhery,SharanNarang,JacobDevlin,
  Maarten Bosma, Gaurav Mishra, Adam Roberts,
  Paul Barham, Hyung Won Chung, Charles Sutton,
  Sebastian Gehrmann, Parker Schuh, Kensen Shi,
  Sasha Tsvyashchenko, Joshua Maynez, Abhishek
  Rao, Parker Barnes, Yi Tay, Noam Shazeer, Vin-
  odkumar Prabhakaran, Emily Reif, Nan Du, Ben
  Hutchinson, Reiner Pope, James Bradbury, Jacob
  Austin,MichaelIsard,GuyGur-Ari,PengchengYin,
  Toju Duke, Anselm Levskaya, Sanjay Ghemawat,
  Sunipa Dev, Henryk Michalewski, Xavier Garcia,
  VedantMisra,KevinRobinson,LiamFedus,Denny
  Zhou,DaphneIppolito,DavidLuan,HyeontaekLim,
  Barret Zoph, Alexander Spiridonov, Ryan Sepassi,
  DavidDohan,ShivaniAgrawal,MarkOmernick,An-
  drew M. Dai, Thanumalayan Sankaranarayana Pil-
  lai,MariePellat,AitorLewkowycz,EricaMoreira,
```

```
  Rewon Child, Oleksandr Polozov, Katherine Lee,
  ZongweiZhou,XuezhiWang,BrennanSaeta,Mark
  Diaz,OrhanFirat,MicheleCatasta,JasonWei,Kathy
  Meier-Hellstern,DouglasEck,JeffDean,SlavPetrov,
  andNoahFiedel.2022. Palm:Scalinglanguagemod-
  elingwithpathways.
Alexandra Chronopoulou, Matthew Peters, and Jesse
  Dodge.2022. Efficienthierarchicaldomainadapta-
  tionforpretrainedlanguagemodels. InProceedings
  ofthe2022ConferenceoftheNorthAmericanChap-
  teroftheAssociationforComputationalLinguistics:
  HumanLanguageTechnologies,pages1336-1351,
  Seattle,UnitedStates.AssociationforComputational
  Linguistics.
Hyung Won Chung, Noah Constant, Xavier Garcia,
  Adam Roberts, Yi Tay, Sharan Narang, and Orhan
  Firat.2023. Unimax: Fairerandmoreeffectivelan-
  guagesamplingforlarge-scalemultilingualpretrain-
  ing. ArXiv,abs/2304.09151.
Christopher Clark, Kenton Lee, Ming-Wei Chang,
  Tom Kwiatkowski, Michael Collins, and Kristina
  Toutanova. 2019. Boolq: Exploring the surprising
  difficultyofnaturalyes/noquestions. arXivpreprint
  arXiv:1905.10044.
PeterClark,IsaacCowhey,OrenEtzioni,TusharKhot,
  AshishSabharwal,CarissaSchoenick,andOyvind
  Tafjord.2018. Thinkyouhavesolvedquestionan-
  swering? tryarc,theai2reasoningchallenge. arXiv
  preprintarXiv:1803.05457.
MikeConover,MattHayes,AnkitMathur,JianweiXie,
  Jun Wan, Sam Shah, Ali Ghodsi, Patrick Wendell,
  MateiZaharia,andReynoldXin.2023. Freedolly:
  Introducingtheworld'sfirsttrulyopeninstruction-
  tunedllm.
Ganqu Cui, Lifan Yuan, Ning Ding, Guanming Yao,
  WeiZhu,YuanNi,GuotongXie,ZhiyuanLiu,and
  MaosongSun.2023. Ultrafeedback: Boostinglan-
  guagemodelswithhigh-qualityfeedback.
JesseDodge,TaylorPrewitt,RemiTachetDesCombes,
  Erika Odmark, Roy Schwartz, Emma Strubell,
  AlexandraSashaLuccioni,NoahA.Smith,Nicole
  DeCario,andWillBuchanan.2022. Measuringthe
  carbonintensityofaiincloudinstances.
WilliamB.DolanandChrisBrockett.2005. Automati-
  callyconstructingacorpusofsententialparaphrases.
  In International Joint Conference on Natural Lan-
  guageProcessing.
Yanai Elazar, Akshita Bhagia, Ian Helgi Magnusson,
  AbhilashaRavichander,DustinSchwenk,AlaneSuhr,
  EvanPeteWalsh, DirkGroeneveld, LucaSoldaini,
  SameerSingh,HannaHajishirzi,NoahA.Smith,and
  Jesse Dodge. 2024. What's in my big data? In
  The Twelfth International Conference on Learning
  Representations.
```

## Page 12

```
LeoGao,StellaBiderman,SidBlack,LaurenceGold-
  ing,TravisHoppe,CharlesFoster,JasonPhang,Ho-
  raceHe, AnishThite, NoaNabeshima, etal.2020.
  The pile: An 800gb dataset of diverse text for lan-
  guagemodeling. arXivpreprintarXiv:2101.00027.
LeoGao,JonathanTow,BaberAbbasi,StellaBiderman,
  SidBlack,AnthonyDiPofi,CharlesFoster,Laurence
  Golding,JeffreyHsu,AlainLeNoac'h,HaonanLi,
  KyleMcDonell,NiklasMuennighoff,ChrisOciepa,
  Jason Phang, Laria Reynolds, Hailey Schoelkopf,
  Aviya Skowron, Lintang Sutawika, Eric Tang, An-
  ishThite, BenWang, KevinWang, andAndyZou.
  2023. A framework for few-shot language model
  evaluation.
SidneyGreenbaumandGeraldNelson.1996. Thein-
  ternational corpus of english (ICE) project. World
  Englishes,15(1):3-15.
DirkGroeneveld,AnasAwadalla,IzBeltagy,Akshita
  Bhagia,IanMagnusson,HaoPeng,OyvindTafjord,
  Pete Walsh, Kyle Richardson, and Jesse Dodge.
  2023. Catwalk: A unified language model evalu-
  ationframeworkformanydatasets. arXivpreprint
  arXiv:2312.10253.
Biyang Guo, Xin Zhang, Ziyuan Wang, Minqi Jiang,
  JinranNie,YuxuanDing,JianweiYue,andYupeng
  Wu.2023. Howcloseischatgpttohumanexperts?
  comparisoncorpus,evaluation,anddetection. arXiv
  preprintarxiv:2301.07597.
SuchinGururangan,MitchellWortsman,SamirYitzhak
  Gadre, Achal Dave, Maciej Kilian, Weijia Shi,
  Jean Mercat, Georgios Smyrnis, Gabriel Ilharco,
  Matt Jordan, Reinhard Heckel, Alex Dimakis, Ali
  Farhadi, Vaishaal Shankar, and Ludwig Schmidt.
  2023. OpenLM: a minimal but performative lan-
  guagemodeling(lm)repository. GitHubrepository.
Thomas Hartvigsen, Saadia Gabriel, Hamid Palangi,
  MaartenSap,DipankarRay,andEceKamar.2022.
  TOXIGEN:ControllingLanguageModelstoGener-
  ateImpliedandAdversarialToxicity. InACL.
Dan Hendrycks, Collin Burns, Steven Basart, Andy
  Zou,MantasMazeika,DawnSong,andJacobStein-
  hardt.2021. Measuringmassivemultitasklanguage
  understanding. ProceedingsoftheInternationalCon-
  ferenceonLearningRepresentations(ICLR).
Hamish Ivison, Yizhong Wang, Valentina Pyatkin,
  Nathan Lambert, Matthew Peters, Pradeep Dasigi,
  Joel Jang, David Wadden, Noah A. Smith, Iz Belt-
  agy, and Hannaneh Hajishirzi. 2023. Camels in a
  changingclimate: Enhancinglmadaptationwithtulu
  2.
Albert Q Jiang, Alexandre Sablayrolles, Antoine
  Roux,ArthurMensch,BlancheSavary,ChrisBam-
  ford,DevendraSinghChaplot,DiegodelasCasas,
  Emma Bou Hanna, Florian Bressand, et al. 2024.
  Mixtralofexperts. arXivpreprintarXiv:2401.04088.
```

```
Andreas Kopf, Yannic Kilcher, Dimitri von Rutte,
  Sotiris Anagnostidis, Zhi Rui Tam, Keith Stevens,
  Abdullah Barhoum, Duc Minh Nguyen, Oliver
  Stanley, Richard Nagyfi, Shahul ES, Sameer Suri,
  DavidAlexandrovichGlushkov,ArnavVarmaDan-
  tuluri,AndrewMaguire,ChristophSchuhmann,Huu
  Nguyen,andAlexanderJulianMattick.2023. Ope-
  nassistant conversations - democratizing large lan-
  guage model alignment. In Thirty-seventh Con-
  ferenceonNeuralInformationProcessingSystems
  DatasetsandBenchmarksTrack.
XuechenLi,TianyiZhang,YannDubois,RohanTaori,
  IshaanGulrajani,CarlosGuestrin,PercyLiang,and
  TatsunoriB.Hashimoto.2023. Alpacaeval: Anau-
  tomatic evaluator of instruction-following models.
  Githubrepository.
Percy Liang, Rishi Bommasani, Tony Lee, Dimitris
  Tsipras, Dilara Soylu, Michihiro Yasunaga, Yian
  Zhang,DeepakNarayanan,YuhuaiWu,AnanyaKu-
  mar, et al. 2022. Holistic evaluation of language
  models. arXivpreprintarXiv:2211.09110.
StephanieLin,JacobHilton,andOwainEvans.2022.
  Truthfulqa: Measuring how models mimic human
  falsehoods. InProceedingsofthe60thAnnualMeet-
  ingoftheAssociationforComputationalLinguistics
  (Volume1: LongPapers),pages3214-3252.
Jian Liu, Leyang Cui, Hanmeng Liu, Dandan Huang,
  YileWang,andYueZhang.2020. Logiqa: Achal-
  lenge dataset for machine reading comprehension
  withlogicalreasoning. CoRR,abs/2007.08124.
Zhengzhong Liu, Aurick Qiao, Willie Neiswanger,
  HongyiWang,BowenTan,TianhuaTao,JunboLi,
  YuqiWang,SuqiSun,OmkarPangarkar,etal.2023.
  Llm360: Towardsfullytransparentopen-sourcellms.
  arXivpreprintarXiv:2312.06550.
Ilya Loshchilov and Frank Hutter. 2019. Decoupled
  weightdecayregularization. InInternationalConfer-
  enceonLearningRepresentations.
AlexandraSashaLuccioni,SylvainViguier,andAnne-
  LaureLigozat.2022. Estimatingthecarbonfootprint
  ofbloom,a176bparameterlanguagemodel.
Ian Magnusson, Akshita Bhagia, Valentin Hofmann,
  LucaSoldaini,AnanyaHarshJha,OyvindTafjord,
  Dustin Schwenk, Evan Pete Walsh, Yanai Elazar,
  Kyle Lo, et al. 2023. Paloma: A benchmark
  for evaluating language model fit. arXiv preprint
  arXiv:2312.10523.
Mitchell P. Marcus, Beatrice Santorini, Mary Ann
  Marcinkiewicz, and Ann Taylor. 1999. Treebank-
  3.
StephenMerity,CaimingXiong,JamesBradbury,and
  RichardSocher.2016. Pointersentinelmixturemod-
  els. ArXiv,abs/1609.07843.
```

## Page 13

```
PauliusMicikevicius,SharanNarang,JonahAlben,Gre-
  goryFrederickDiamos,ErichElsen,DavidGarcia,
  BorisGinsburg,MichaelHouston,OleksiiKuchaiev,
  GaneshVenkatesh,andHaoWu.2017. Mixedpreci-
  siontraining. ArXiv,abs/1710.03740.
TodorMihaylov,PeterClark,TusharKhot,andAshish
  Sabharwal.2018. Canasuitofarmorconductelec-
  tricity? anewdatasetforopenbookquestionanswer-
  ing. arXivpreprintarXiv:1809.02789.
TomasMikolov,IlyaSutskever,KaiChen,GregoryS.
  Corrado,andJeffreyDean.2013. Distributedrepre-
  sentationsofwordsandphrasesandtheircomposi-
  tionality. InNeuralInformationProcessingSystems.
Swaroop Mishra, Daniel Khashabi, Chitta Baral, and
  Hannaneh Hajishirzi. 2022. Cross-task generaliza-
  tionvianaturallanguagecrowdsourcinginstructions.
  In Proceedings of the 60th Annual Meeting of the
  AssociationforComputationalLinguistics(Volume
  1: LongPapers),pages3470-3487,Dublin,Ireland.
  AssociationforComputationalLinguistics.
MosaicMLNLPTeam.2023. Introducingmpt-7b: A
  newstandardforopen-source,commerciallyusable
  llms. Accessed: 2023-05-05.
NiklasMuennighoff,AlexanderMRush,BoazBarak,
  TevenLeScao,AleksandraPiktus,NouamaneTazi,
  Sampo Pyysalo, Thomas Wolf, and Colin Raffel.
  2023. Scaling data-constrained language models.
  arXivpreprintarXiv:2305.16264.
DavideNunes.2020. Preprocessedpenntreebank.
OpenAI. 2023. Gpt-4 technical report. ArXiv,
  abs/2303.08774.
LongOuyang,JeffreyWu,XuJiang,DiogoAlmeida,
  CarrollWainwright,PamelaMishkin,ChongZhang,
  SandhiniAgarwal,KatarinaSlama,AlexRay,John
  Schulman,JacobHilton,FraserKelton,LukeMiller,
  Maddie Simens, Amanda Askell, Peter Welinder,
  PaulFChristiano,JanLeike,andRyanLowe.2022.
  Traininglanguagemodelstofollowinstructionswith
  humanfeedback. InAdvancesinNeuralInformation
  ProcessingSystems,volume35,pages27730-27744.
  CurranAssociates,Inc.
Antonis Papasavva, Savvas Zannettou, Emiliano De
  Cristofaro,GianlucaStringhini,andJeremyBlack-
  burn. 2020. Raiders of the lost kek: 3.5 years of
  augmented 4chan posts from the politically incor-
  rectboard. ProceedingsoftheInternationalAAAI
  ConferenceonWebandSocialMedia,14:885-894.
David Patterson, Joseph Gonzalez, Quoc Le, Chen
  Liang, Lluis-Miquel Munguia, Daniel Rothchild,
  DavidSo,MaudTexier,andJeffDean.2021. Carbon
  emissionsandlargeneuralnetworktraining.
Guilherme Penedo, Quentin Malartic, Daniel Hess-
  low, Ruxandra-Aimee Cojocaru, Alessandro Cap-
  pelli, Hamza Alobeidli, Baptiste Pannier, Ebtesam
```

```
  Almazrouei,andJulienLaunay.2023. Therefined-
  webdatasetforfalconllm: Outperformingcurated
  corpora with web data, and web data only. ArXiv,
  abs/2306.01116.
MatthewE.Peters,MarkNeumann,MohitIyyer,Matt
  Gardner,ChristopherClark,KentonLee,andLuke
  Zettlemoyer.2018. Deepcontextualizedwordrepre-
  sentations. ArXiv,abs/1802.05365.
MohammadTaherPilehvarandJoseCamacho-Collados.
  2018. Wic: 10, 000 example pairs for eval-
  uating context-sensitive representations. CoRR,
  abs/1808.09121.
Jack W. Rae, Sebastian Borgeaud, Trevor Cai, Katie
  Millican, Jordan Hoffmann, Francis Song, John
  Aslanides, Sarah Henderson, Roman Ring, Susan-
  nah Young, Eliza Rutherford, Tom Hennigan, Ja-
  cobMenick,AlbinCassirer,RichardPowell,George
  van den Driessche, Lisa Anne Hendricks, Mari-
  beth Rauh, Po-Sen Huang, Amelia Glaese, Jo-
  hannes Welbl, Sumanth Dathathri, Saffron Huang,
  JonathanUesato,JohnMellor,IrinaHiggins,Anto-
  niaCreswell,NatMcAleese,AmyWu,ErichElsen,
  SiddhantJayakumar,ElenaBuchatskaya,DavidBud-
  den,EsmeSutherland,KarenSimonyan,MichelaPa-
  ganini,LaurentSifre,LenaMartens,XiangLorraine
  Li, Adhiguna Kuncoro, Aida Nematzadeh, Elena
  Gribovskaya,DomenicDonato,AngelikiLazaridou,
  ArthurMensch,Jean-BaptisteLespiau,MariaTsim-
  poukelli,NikolaiGrigorev,DougFritz,ThibaultSot-
  tiaux,MantasPajarskas,TobyPohlen,ZhitaoGong,
  DanielToyama,CypriendeMassond'Autume,Yujia
  Li,TayfunTerzi,VladimirMikulik,IgorBabuschkin,
  Aidan Clark, Diego de Las Casas, Aurelia Guy,
  Chris Jones, James Bradbury, Matthew Johnson,
  Blake Hechtman, Laura Weidinger, Iason Gabriel,
  WilliamIsaac,EdLockhart,SimonOsindero,Laura
  Rimell,ChrisDyer,OriolVinyals,KareemAyoub,
  JeffStanway,LorrayneBennett,DemisHassabis,Ko-
  rayKavukcuoglu,andGeoffreyIrving.2022. Scaling
  languagemodels: Methods,analysis&insightsfrom
  traininggopher.
RafaelRafailov,ArchitSharma,EricMitchell,Christo-
  pherDManning,StefanoErmon,andChelseaFinn.
  2023. Directpreferenceoptimization:Yourlanguage
  modelissecretlyarewardmodel. InThirty-seventh
  ConferenceonNeuralInformationProcessingSys-
  tems.
ColinRaffel,NoamShazeer,AdamRoberts,Katherine
  Lee,SharanNarang,MichaelMatena,YanqiZhou,
  WeiLi,andPeterJ.Liu.2020. Exploringthelimits
  oftransferlearningwithaunifiedtext-to-texttrans-
  former. J.Mach.Learn.Res.,21(1).
Samyam Rajbhandari, Jeff Rasley, Olatunji Ruwase,
  andYuxiongHe.2019. Zero: Memoryoptimizations
  towardtrainingtrillionparametermodels. SC20: In-
  ternationalConferenceforHighPerformanceCom-
  puting,Networking,StorageandAnalysis,pages1-
  16.
```

## Page 14

```
MachelReid,VictorZhong,SuchinGururangan,and
  LukeZettlemoyer.2022. M2D2: Amassivelymulti-
  domainlanguagemodelingdataset. InProceedings
  of the 2022 Conference on Empirical Methods in
  NaturalLanguageProcessing,pages964-975,Abu
  Dhabi,UnitedArabEmirates.AssociationforCom-
  putationalLinguistics.
ManoelHortaRibeiro,JeremyBlackburn,BarryBrad-
  lyn, Emiliano De Cristofaro, Gianluca Stringhini,
  Summer Long, Stephanie Greenberg, and Savvas
  Zannettou.2021. Theevolutionofthemanosphere
  across the web. Proceedings of the International
  AAAIConferenceonWebandSocialMedia,15:196-
  207.
Ronald Rosenfeld. 2000. Two decades of statistical
  language modeling: Where do we go from here?
  ProceedingsoftheIEEE,88(8):1270-1278.
KeisukeSakaguchi,RonanLeBras,ChandraBhagavat-
  ula,andYejinChoi.2021. Winogrande: Anadver-
  sarialwinogradschemachallengeatscale. Commu-
  nicationsoftheACM,64(9):99-106.
Victor Sanh, Albert Webson, Colin Raffel, Stephen
  Bach, Lintang Sutawika, Zaid Alyafeai, Antoine
  Chaffin, Arnaud Stiegler, Arun Raja, Manan Dey,
  M Saiful Bari, Canwen Xu, Urmish Thakker,
  ShanyaSharmaSharma,ElizaSzczechla,Taewoon
  Kim, Gunjan Chhablani, Nihal Nayak, Debajyoti
  Datta,JonathanChang,MikeTian-JianJiang,Han
  Wang,MatteoManica,ShengShen,ZhengXinYong,
  HarshitPandey,RachelBawden,ThomasWang,Tr-
  ishala Neeraj, Jos Rozen, Abheesht Sharma, An-
  dreaSantilli,ThibaultFevry,JasonAlanFries,Ryan
  Teehan,TevenLeScao,StellaBiderman,LeoGao,
  ThomasWolf,andAlexanderMRush.2022. Multi-
  taskpromptedtrainingenableszero-shottaskgener-
  alization. InInternationalConferenceonLearning
  Representations.
NoamM.Shazeer.2020. Gluvariantsimprovetrans-
  former. ArXiv,abs/2002.05202.
LucaSoldaini,RodneyKinney,AkshitaBhagia,Dustin
  Schwenk,DavidAtkinson,RussellAuthur,BenBo-
  gin,KhyathiChandu,JenniferDumas,YanaiElazar,
  ValentinHofmann,AnanyaHarshJha,SachinKumar,
  LiLucy,XinxiLyu,NathanLambert,IanMagnusson,
  Jacob Morrison, Niklas Muennighoff, Aakanksha
  Naik, Crystal Nam, Matthew E. Peters, Abhilasha
  Ravichander,KyleRichardson,ZejiangShen,Emma
  Strubell,NishantSubramani,OyvindTafjord,Pete
  Walsh,LukeZettlemoyer,NoahA.Smith,Hannaneh
  Hajishirzi,IzBeltagy,DirkGroeneveld,JesseDodge,
  andKyleLo.2024. Dolma:anOpenCorpusofThree
  TrillionTokensforLanguageModelPretrainingRe-
  search. arXivpreprint.
EmmaStrubell,AnanyaGanesh,andAndrewMcCal-
  lum. 2019. Energy and policy considerations for
  deep learning in NLP. In Proceedings of the 57th
  AnnualMeetingoftheAssociationforComputational
  Linguistics,pages3645-3650,Florence,Italy.Asso-
  ciationforComputationalLinguistics.
```

```
JianlinSu,YuLu,ShengfengPan,BoWen,andYunfeng
  Liu. 2021. Roformer: Enhanced transformer with
  rotarypositionembedding. ArXiv,abs/2104.09864.
Rohan Taori, Ishaan Gulrajani, Tianyi Zhang, Yann
  Dubois,XuechenLi,CarlosGuestrin,PercyLiang,
  andTatsunoriB.Hashimoto.2023. Stanfordalpaca:
  An instruction-following llama model. https://
  github.com/tatsu-lab/stanford_alpaca.
Teknium1. 2023. Gpteacher. https://github.com/
  teknium1/GPTeacher.
Together Computer. 2023. RedPajama: An Open
  Source Recipe to Reproduce LLaMA training
  dataset.
HugoTouvron,ThibautLavril,GautierIzacard,Xavier
  Martinet,Marie-AnneLachaux,TimotheeLacroix,
  BaptisteRoziere,NamanGoyal,EricHambro,Faisal
  Azhar,AurelienRodriguez,ArmandJoulin,Edouard
  Grave,andGuillaumeLample.2023a. Llama: Open
  and efficient foundation language models. ArXiv,
  abs/2302.13971.
Hugo Touvron, Louis Martin, Kevin Stone, Peter Al-
  bert, Amjad Almahairi, Yasmine Babaei, Nikolay
  Bashlykov,SoumyaBatra,PrajjwalBhargava,Shruti
  Bhosale,DanBikel,LukasBlecher,CristianCanton
  Ferrer,MoyaChen,GuillemCucurull,DavidEsiobu,
  JudeFernandes,JeremyFu,WenyinFu,BrianFuller,
  CynthiaGao,VedanujGoswami,NamanGoyal,An-
  thonyHartshorn,SagharHosseini,RuiHou,Hakan
  Inan,MarcinKardas,ViktorKerkez,MadianKhabsa,
  IsabelKloumann,ArtemKorenev,PunitSinghKoura,
  Marie-AnneLachaux,ThibautLavril,JenyaLee,Di-
  anaLiskovich,YinghaiLu,YuningMao,XavierMar-
  tinet,TodorMihaylov,PushkarMishra,IgorMoly-
  bog, Yixin Nie, Andrew Poulton, Jeremy Reizen-
  stein,RashiRungta,KalyanSaladi,AlanSchelten,
  Ruan Silva, Eric Michael Smith, Ranjan Subrama-
  nian, Xiaoqing Ellen Tan, Binh Tang, Ross Tay-
  lor, Adina Williams, Jian Xiang Kuan, Puxin Xu,
  ZhengYan,IliyanZarov,YuchenZhang,AngelaFan,
  Melanie Kambadur, Sharan Narang, Aurelien Ro-
  driguez,RobertStojnic,SergeyEdunov,andThomas
  Scialom. 2023b. Llama 2: Open foundation and
  fine-tunedchatmodels.
MariaUbierna,CristinaDiezSantos,andSaraMercier-
  Blais. 2022. Water Security and Climate Change:
  HydropowerReservoirGreenhouseGasEmissions,
  pages69-94.SpringerSingapore,Singapore.
Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob
  Uszkoreit, Llion Jones, Aidan N Gomez, ? ukasz
  Kaiser,andIlliaPolosukhin.2017. Attentionisall
  youneed. InAdvancesinNeuralInformationPro-
  cessingSystems,volume30.CurranAssociates,Inc.
David Vilares and Carlos Gomez-Rodriguez. 2019.
  HEAD-QA:Ahealthcaredatasetforcomplexreason-
  ing. InProceedingsofthe57thAnnualMeetingof
  theAssociationforComputationalLinguistics,pages
  960-966,Florence,Italy.AssociationforComputa-
  tionalLinguistics.
```

## Page 15

```
Alex Wang, Amanpreet Singh, Julian Michael, Felix
  Hill, Omer Levy, and Samuel R. Bowman. 2018.
  Glue: A multi-task benchmark and analysis plat-
  form for natural language understanding. ArXiv,
  abs/1804.07461.
Yizhong Wang, Hamish Ivison, Pradeep Dasigi, Jack
  Hessel, Tushar Khot, Khyathi Raghavi Chandu,
  DavidWadden,KelseyMacMillan,NoahA.Smith,
  Iz Beltagy, and Hannaneh Hajishirzi. 2023. How
  farcancamelsgo? exploringthestateofinstruction
  tuningonopenresources.
JasonWei,MaartenBosma,VincentZhao,KelvinGuu,
  Adams Wei Yu, Brian Lester, Nan Du, Andrew M.
  Dai,andQuocVLe.2022. Finetunedlanguagemod-
  elsarezero-shotlearners. InInternationalConfer-
  enceonLearningRepresentations.
JohannesWelbl,NelsonFLiu,andMattGardner.2017.
  Crowdsourcing multiple choice science questions.
  arXivpreprintarXiv:1707.06209.
Carole-Jean Wu, Ramya Raghavendra, Udit Gupta,
  BilgeAcun,NewshaArdalani,KiwanMaeng,Glo-
  ria Chang, Fiona Aga Behram, James Huang,
  Charles Bai, Michael Gschwind, Anurag Gupta,
  Myle Ott, Anastasia Melnikov, Salvatore Candido,
  DavidBrooks,GeetaChauhan,BenjaminLee,Hsien-
  HsinS.Lee,BugraAkyildiz,MaximilianBalandat,
  JoeSpisak,RaviJain,MikeRabbat,andKimHazel-
  wood.2022. Sustainableai: Environmentalimplica-
  tions,challengesandopportunities.
Can Xu, Qingfeng Sun, Kai Zheng, Xiubo Geng,
  Pu Zhao, Jiazhan Feng, Chongyang Tao, Qingwei
  Lin, and Daxin Jiang. 2024. WizardLM: Empow-
  ering large pre-trained language models to follow
  complexinstructions. InTheTwelfthInternational
  ConferenceonLearningRepresentations.
CanwenXu,DayaGuo,NanDuan,andJulianMcAuley.
  2023. Baize: An open-source chat model with
  parameter-efficient tuning on self-chat data. arXiv
  preprintarXiv:2304.01196.
SavvasZannettou,BarryBradlyn,EmilianoDeCristo-
  faro,HaewoonKwak,MichaelSirivianos,Gianluca
  Stringini,andJeremyBlackburn.2018. Whatisgab:
  Abastionoffreespeechoranalt-rightechochamber.
  InCompanionProceedingsoftheTheWebConfer-
  ence2018,WWW'18,page1007-1014,Republic
  and Canton of Geneva, CHE. International World
  WideWebConferencesSteeringCommittee.
Rowan Zellers, Ari Holtzman, Yonatan Bisk, Ali
  Farhadi, and Yejin Choi. 2019. Hellaswag: Can a
  machinereallyfinishyoursentence? arXivpreprint
  arXiv:1905.07830.
BiaoZhangandRicoSennrich.2019. Rootmeansquare
  layernormalization. ArXiv,abs/1910.07467.
Susan Zhang, Stephen Roller, Naman Goyal, Mikel
  Artetxe,MoyaChen,ShuohuiChen,ChristopherDe-
  wan,MonaDiab,XianLi,XiVictoriaLin,TodorMi-
  haylov,MyleOtt,SamShleifer,KurtShuster,Daniel
```

Simig, Punit Singh Koura, Anjali Sridhar, Tianlu
Wang,andLukeZettlemoyer.2022. Opt: Openpre-
trainedtransformerlanguagemodels.
YanliZhao,AndrewGu,RohanVarma,LiangchenLuo,
Chien chin Huang, Min Xu, Less Wright, Hamid
Shojanazeri,MyleOtt,SamShleifer,AlbanDesmai-
son,CanBalioglu,BernardNguyen,GeetaChauhan,
YuchenHao,andShenLi.2023. Pytorchfsdp: Expe-
riencesonscalingfullyshardeddataparallel. Proc.
VLDBEndow.,16:3848-3860.

## Page 16

A TrainingSettings supercomputer, which runs on 100% renewable,
carbon-neutralenergy,soweassumeacarbonin-
Table 5 summarizes the model architecture and
tensity factor of 0. LUMI is powered entirely by
theoptimizerparametersofOLMo-7Baswellas
hydroelectric power and some sources (Ubierna
recentsimilar-sizedmodels.
et al., 2022) measure the carbon intensity factor
ofhydroelectricpowertobe0.024, whichwould
B PowerConsumptionandCarbon
13
imply total carbon emissions of 3.54 tCO eq.
Footprint 2
However,werelyontheofficialLUMIdataforour
Followingpreviousliterature(Strubelletal.,2019; calculations, and thus we estimate total pretrain-
Pattersonetal.,2021;Wuetal.,2022;Dodgeetal., ing emissions of 69.78 tCO eq. 14 In Table 6 we
2
2022),weestimatethetotalenergyconsumedand compareourmodelswithotherpreviouslyreleased
carbon released while pretraining our models by modelsbasedonpubliclyavailableinformation.
calculatingthetotalpowerconsumptionrequired We hope that openly releasing our models can
for training, and then multiplying it by the car- reducefutureemissionsbyallowingotherstoavoid
bon emission intensity of the power grid where theneedtopretrainmodelsfromscratch,andgive
themodelwastrained. Whilereportingtheseop- insightsintothetruecostofdevelopingstateofthe
erational emissions is standard practice, it does artmodels. Wealsohighlightthatourestimatesare
not account for other sources of emissions such lower bounds, because they do not include other
as the embodied emissions due to the manufac- criticalpiecesofdevelopmentsuchasdebugging,
turing, transportation, and disposal of hardware hyperparametertuning,anddowntime.
anddatacenterinfrastructure,lifetimeoperational
C AdditionalEvaluation
emissionsduetouse,reboundeffects,orotheren-
vironmentalimpactssuchaswaterconsumptionor
Additional perplexity results In Figure 3 we
mining. Thus our estimates should be viewed as
provide results for each of the 7 data sources in
lowerbounds.
Paloma(Magnussonetal.,2023)thatareexcluded
We calculate the total power consumption for
from the combined metric in Figure 2. Some of
ourmodelsbymeasuringthepowerconsumption
these sources such as Pile (Gao et al., 2020) and
ofasinglenodeevery25ms,calculatinganaverage
ICE (Greenbaum and Nelson, 1996) are not pub-
acrosstheentiretrainingrun,andmultiplyingby
licly available at this time. Dolma 100 Program-
thetotalnumberofnodes. Wethenaccountforthe
mingLanguages(Soldainietal.,2024)consistsof
energyefficiencyofthedatacenterbymultiplying
codedatathatisnotsupportedbythedecontamina-
theprevioustotalbyapowerusageeffectiveness
tionapproachusedinPaloma. TwitterAAE(Blod-
(PUE) factor, which we set to 1.1, representing a
gettetal.,2016),alongwithICE,aredatasetsfor
conservative 10% energy consumption overhead
targetedanalysesofdisparitiesinperformancebe-
1011
typicalofenergyefficientdatacenters. Weesti-
tweendifferentdialectsandassuchshouldbeeval-
matethatpretrainingour7Bmodelsconsumed239
uatedseparately. Andfinally,theManosphere,Gab,
MWhofenergy.
and4chancorpora(Ribeiroetal.,2021;Zannettou
Tocalculatecarbonemissions,wemultiplythe
et al., 2018; Papasavva et al., 2020) are intended
totalpowerconsumptionbyacarbonintensityfac-
to examine model fit to language from fringe on-
tor,measuredinkgCO emittedperKWh,based
2 linecommunitiesthatarestudiedforprevalenthate
on the physical location of the data center where
speech and toxicity. Thus minimizing perplexity
each model was trained. The model trained on
onthesefringecorporaisnotalwaysdesirable.
A100-40GBGPUswastrainedinAustralia,sowe
OnenotableresulthereisthatOLMo-7Bismuch
assumeacarbonintensityfactorof0.610,thena-
farther ahead of the other models on Dolma 100
12
tionalaverageforAustraliain2022. Themodel
ProgrammingLanguages(100PLs). Notethatthis
trainedonMI250XGPUswastrainedintheLUMI
effectmaybedueinparttounderestimationfrom
10https://www.nrel.gov/computational-science/ contamination,asdecontaminatingcodedataisbe-
measuring-efficiency-pue.html yond the scope of the method in Paloma. At the
11https://www.google.com/about/datacenters/
efficiency/ 13https://www.lumi-supercomputer.eu
12https://www.cleanenergyregulator. 14ThesemetricswereinpartcollectedusingCarbonara's
gov.au/Infohub/Markets/Pages/qcmr/ AI agent and monitoring platform. Learn more at: https:
december-quarter-2022/Emissions-Reduction.aspx //trycarbonara.com

## Page 17

```
                  OLMo-7B  LLaMA2-7B OpenLM-7B Falcon-7B PaLM-8B
     Dimension    4096     4096      4096     4544   4096
     Numheads     32       32        32       71     16
     Numlayers    32       32        32       32     32
     MLPratio     ?8/3     ?8/3      ?8/3     4      4
     Layernormtype non-parametric RMSNorm parametric parametric parametric
     Positionalembeddings RoPE RoPE  RoPE     RoPE   RoPE
     Attentionvariant full GQA       full     MQA    MQA
     Biases       none     none      inLNonly inLNonly none
     Blocktype    sequential sequential sequential parallel parallel
     Activation   SwiGLU   SwiGLU    SwiGLU   GeLU   SwiGLU
     Sequencelength 2048   4096      2048     2048   2048
     Batchsize(instances) 2160 1024  2048     2304   512
     Batchsize(tokens) ?4M ?4M       ?4M      ?4M    ?1M
     Weighttying  no       no        no       no     yes
     Warmupsteps  5000     2000      2000     1000
     PeakLR       3.0E-04  3.0E-04   3.0E-04  6.0E-04
     MinimumLR    3.0E-05  3.0E-05   3.0E-05  1.2E-05
     Weightdecay  0.1      0.1       0.1      0.1
     Beta1        0.9      0.9       0.9      0.99
     Beta2        0.95     0.95      0.95     0.999
     Epsilon      1.0E-05  1.0E-05   1.0E-05  1.0E-05
     LRschedule   linear   cosine    cosine   cosine
     Gradientclipping global1.0 global1.0 global1.0 global1.0
     Gradientreducedtype FP32 FP32   FP32     BF16
     Optimizerstatedtype FP32 mostlikelyFP32 FP32 FP32
Table5:LMarchitectureandoptimizercomparisonatthe7-8Bscale.Inthe"layernormtype"row,"parametric"and
"non-parametric"refertotheusuallayernormimplementationwithandwithoutadaptivegainandbias,respectively.
AllmodelsaretrainedusingAdamW.
```

same time other models that are trained on code
data from GitHub such as RPJ-INCITE-7B, that
arejustaslikelytohavecontamination,fairmuch
worse. AnotherfactorthenisthatOLMo-7Btrains
oncodedatawithexactlythesamepost-processing
asthatin100PLswhilethecodedatainothermod-
elswillhavebeenprocesseddifferently. Similarly,
Pileevaluationdemonstratesthesein-distribution
andpotentialcontaminationeffectsasPythia-6.9B
achievestopperformancedespitebeingtrainedon
almost an order of magnitude fewer tokens than
OLMo-7B.
Theresultsontheremaining5targetedsources
should be interpreted with care, as Paloma often
findsthatperplexityonthesesourcesisdominated
by superficial features such as low average doc-
ument length rather than fit to that which would
actuallybesalienttomembersofthesespeechcom-
munities. TwitterAAE and Gab have among the
shortestdocumentsinPalomacontributingtoun-
usually high bits per byte in this figure. Other
thanthesetwo,themodelsarenotablyveryclosely
groupedinadatascalingtrendinICE,Manosphere,
and4chan.

Additional end-task results Next, in Table 7,
we provide results from zero-shot evaluation of

OLMo-7B on 6 additional end-tasks apart from
the8inourcoreevaluationsuite. Thesetasksare
headqa_en(VilaresandGomez-Rodriguez,2019),
logiqa(Liuetal.,2020),mrpc(DolanandBrock-
ett,2005),qnli(Wangetal.,2018),wic(Pilehvar
and Camacho-Collados, 2018), and wnli (Wang
etal.,2018).

We note, however, that in contrast to our core
evaluationsetdescribedinSection4.1,wefound
theseadditionalend-taskstohavelessstableperfor-
manceduringmodeldevelopment,andtoprovidea
limitedsignal. ThisisillustratedinFigure4,where
weseetheprogressoftaskperformancethroughout
trainingtobemorerandom(comparewiththemore
stableupwardtrendsinFigure1). Whiletaskssuch
asmrpcandwicappearmorestable,theyoffered
additionaldifficultiesrelatedtoperformancebeing
tiedtorandomchance(e.g.,wic)orthetendencyof
modelstomakespuriouspredictions(e.g.,always
predicting a single label) that either inflate or de-
flateperformanceduetodatasetclassimbalances
(e.g.,mrpc). Wethereforecautionagainstrelying
tooheavilyonthesetaskswhenmeasuringmodel
performance throughout training and comparing
models.

## Page 18

|  | GPUType | GPUPower Consumption (MWh) | Power Usage Effectiveness | Carbon Intensity (kgCO2e/KWh) | Carbon Emissions (tCO2eq) |
| --- | --- | --- | --- | --- | --- |
| Gopher-280B | TPUv3 | 1,066 | 1.08 | 0.330 | 380 |
| BLOOM-176B | A100-80GB | 433 | 1.2 | 0.057 | 30 |
| OPT-175B | A100-80GB | 324 | 1.1 | 0.231 | 82 |
| T5-11B | TPUv3 | 77 | 1.12 | 0.545 | 47 |
| LLaMA-7B | A100-80GB | 33 | 1.1 | 0.385 | 14 |
| LLaMA2-7B | A100-80GB | 74 | 1.1 | 0.385 | 31 |
| OLMo-7B | MI250X | 135 | 1.1 | 0.000* | 0* |
| OLMo-7B | A100-40GB | 104 | 1.1 | 0.610 | 70 |

Table 6: CO emissions during pretraining. We estimate the total carbon emissions for various models using
2
publiclyavailabledataonPUE,carbonintensityoflocalpowergrid,andreportedpowerconsumption. Numbersfor
Gopher-280B(Raeetal.,2022),BLOOM-176B(Luccionietal.,2022),OPT-175B(Zhangetal.,2022),T5-11B
(Pattersonetal.,2021),LLaMA(Touvronetal.,2023a),andLLaMA2(Touvronetal.,2023b)aretakenfromtheir
respectivepapers. SeeSectionBfordetailsonhowtCO2eqwascalculated.
*LUMIrunsentirelyonhydroelectricpower13andsomeestimates(Ubiernaetal.,2022)measuretheintensityfactor
ofhydroelectricpowertobe0.024,implyingtotalemissionsof3.54tCO eq.
2

|  |  | headqa_en | logiqa | mrpc | qnli | wic | wnli | avg. |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  | 38.6 23.7 62.8 49.8 49.5 47.9 38.7 19.5 68.6 50.1 49.1 52.1 39.5 26.1 69.1 49.4 49.8 45.1 37.4 22.9 67.7 52.1 48.1 47.9 40.1 21.5 65.4 53.8 55.0 38.0 36.9 27.8 58.8 53.8 48.9 57.8 |  |  |  |  |  |  |
|  | LLaMA-7B | 38.7 | 19.5 | 68.6 | 50.1 | 49.1 | 52.1 | 46.4 |
|  | LLaMA2-7B | 39.5 | 26.1 | 69.1 | 49.4 | 49.8 | 45.1 | 46.5 |
|  | MPT-7B | 37.4 | 22.9 | 67.7 | 52.1 | 48.1 | 47.9 | 46.0 |
|  | Pythia-6.9B | 40.1 | 21.5 | 65.4 | 53.8 | 55.0 | 38.0 | 45.6 |
|  | RPJ-INCITE-7B | 36.9 | 27.8 | 58.8 | 53.8 | 48.9 | 57.8 | 47.3 |
| OLMo-7B |  | 37.3 | 23.4 | 68.4 | 49.1 | 50.2 | 56.3 | 47.5 |

Table7: Zero-shotevaluationofOLMo-7Bon6additionalend-tasksapartfromthe8presentinourcoreevaluation
suite. Onceagain,wecompareOLMo-7Bto6othermodelcheckpointswhicharepubliclyavailable. Wefindthat
OLMo-7Boutperformstheothermodelsonaggregatetakenover6additionalend-tasksfromthistable,however
thesetaskswerealsofoundtoprovidelimitedsignalduringtraining(seeFigure4).

D AdaptationTrainingDetails data about OLMo. Data is publically avail-
14
able.
We use the following hyperparameters when in-
struction tuning OLMo. These were chosen Afterinstructionfinetuning,wethenusethefol-
throughsmallpilotexperiments. lowinghyperparametersforDPOtraining,follow-
- Learningrate: 2x10 ?6 ingIvisonetal.(2023):
- Epochs: 3 - Learningrate: 5x10 ?7
- Warmup: Linear warmup for the first 3% of - ?: 0.1
totaltrainingtime,andthenlinearcooldown - Epochs: 3
toalearningrateof0overtheremainingsteps.
- Warmup: Linearwarmupforthefirst10%of
- Weightdecay: 0 totaltrainingtime,andthenlinearcooldown
- Gradientclipping: 0 toalearningrateof0overtheremainingsteps.
- Maximumsequencelength: 2048 - Weightdecay: 0
- Data: TULU V2 SFT mix, resplit such that - Gradientclipping: 0
long conversations are split into 2048-token 14https://huggingface.co/datasets/allenai/
chunksandreplacingthehardcodedsplitwith tulu-v2-sft-mixture-olmo-2048

## Page 19

10 100 1000 10000
22.1

28.0

55.0
Pile

10 100 1000 10000
28.0

55.0

73.0
100 PLs

10 100 1000 10000
22.1

00.1

28.0
ICE

10 100 1000 10000
60.4

27.2

28.1
Twitter AAE

10 100 1000 10000
53.1

11.1

56.1

53.1
Gab

10 100 1000 10000
53.1

11.1

09.0
4chan
etyB
reP
stiB

Tokens Seen (billions)
Figure3: Bitsperbyteforeachofthe7remainingPalomadatasourcesnotaggregatedinFigure2.

500 1000 1500 2000 2500
83

63

43
headqa_en

500 1000 1500 2000 2500
42

22

02
logiqa

500 1000 1500 2000 2500
06

54
mrpc

500 1000 1500 2000 2500
45

15
qnli

500 1000 1500 2000 2500
2.05

0.05

8.94
wic

500 1000 1500 2000 2500
46

65

84
wnli

Tokens Seen (billions)
ycaruccA

Figure4: AccuracyscoreprogressionofOLMo-7Bon6additionalend-tasks. Theperformanceoftheseadditional
end-taskswasunstableandprovidedlimitedsignalduringmodeldevelopment.

- Maximumsequencelength: 2048

15
chosenandrejectedpairs.

- Data: AmodifiedformofUltraFeedback(Cui
et al., 2023), with TruthfulQA prompts re-
moved. Weusedthe'fixed'variantreleased
by Argilla, which uses the average of GPT-
generated aspect-based scores to determine

15https://huggingface.co/datasets/argilla/
ultrafeedback-binarized-preferences-cleaned

## Page 20

```
E  AdaptationEvaluationandModel
   details
We choose the models in Table 4 by choosing
the 'canonical' best versions (that is, the best
instruction-tunedorotherwiseadaptedmodelsre-
leasedbythesameorganisation)ofthebasemodels
we compare against in Table 3. We additionally
compareto TULU 2toshowthecurrentbestmod-
els trained using the TULU mix used to finetune
OLMo. We display evaluations on MMLU, Al-
pacaEval,ToxiGen,andTruthfulnesstofocuson
displaying how instruction tuning can generally
help capabilities (MMLU), how the models per-
forminanopen-endedchatsetting(AlpacaEval),
and to test how instruction tuning aids in model
safetyandtruthfulness(AlpacaEval,ToxiGen). We
additionallyreportOLMo'sperformanceoverthe
entire TULUevaluationsuiteinTable8.
  We provide a brief description of each model
evaluatedinTable4below. Forallmodels,weuse
theprovidedchattemplateforpromptformatting
whenavailable.
- MPT Chat: A version of MPT 7B fine-
 tuned on the ShareGPT-Vicuna (Chiang
 et al., 2023), HC3 (Guo et al., 2023), Al-
 paca (Taori et al., 2023), HH-RLHF (Bai
 et al., 2022), and Evol-Instruct (Xu et al.,
 2024) datasets. Retrieved from https:
 //huggingface.co/mosaicml/mpt-7b-chat.
- Falcon Instruct: A version of Falcon
 7B  finetuned on the Baize (Xu et al.,
 2023), GPT4All (Anand et al., 2023),
 GPTeacher(Teknium1,2023),andRefined-Web
 English(Penedoetal.,2023)datasets. Retrieved
 from   https://huggingface.co/tiiuae/
 falcon-7b-instruct.
- RPJ-INCITE Chat: A version of RPJ-INCITE
 7B finetuned on the OASST1 (Kopf et al.,
 2023) and Dolly V2 (Conover et al.,
 2023) datasets. Retrieved from https:
 //huggingface.co/togethercomputer/
 RedPajama-INCITE-7B-Chat.
- Llama-2 Chat: A version of Llama 2 7B fine-
 tuned on a mixture of instruction datasets and
 further trained with RLHF. We refer the reader
 toTouvronetal.(2023b)forfurtherdetails.
- TULU2: AversionofLlama27Bfinetunedona
 mixtureofinstructiondatasets(theTULU2mix).
```

We refer the reader to Ivison et al. (2023) for
furtherdetails.
- TULU2+DPO:TULU2furthertrainedwithDPO
ontheUltraFeedbackdataset(Cuietal.,2023).
We refer the reader to Ivison et al. (2023) for
furtherdetails.
- OLMo+SFT:AversionofOLMo7Bfintunedon
thesamedataas TULU2.
- OLMo+SFT+DPO:OLMo+SFTfurthertrained
with DPO on the UltraFeedback dataset (Cui
etal.,2023).
We additionally provide a brief description of
eachevaluationsettingfromTable4:
- MMLU:WeusetheofficialMMLU(Hendrycks
et al., 2021) evaluation script and prompts
availableathttps://github.com/hendrycks/
test,withmodificationstoallowforbatchpro-
cessing. Weevaluateusing0few-shotexamples,
followingtheoriginalsetupofMMLU.Wereport
averageaccuracyacrosstestexamples.
- ToxiGen: WefollowthesetupinTouvronetal.
(2023b),butusetheoriginalsetofpromptsfrom
Hartvigsen et al. (2022), which are designed
to elicit toxic generations for certain groups.
We take only the prompts designed to produce
toxiclanguage('hateful'prompts)anduse500
prompts per group to reduce evaluation costs.
For base language models, we pass in the orig-
inal ToxiGen prompts unchanged and greedily
decode up to the first new line (or a maximum
of 512 tokens). For instruction-tuned models,
we place the prompt in the corresponding tem-
plate,andaskthemodeltocompletetheprompt,
untilthemodelgeneratesastoptoken(oramax-
imum of 512 tokens). We pass the generated
textintoaroberta-largemodeltrainedtodetect
toxiccontentfinetunedaspartofHartvigsenetal.
16
(2022). Wethenreportthepercentageofgen-
erationsdeemedtoxicbytheclassifier.
- TruthfulQA:FollowingTouvronetal.(2023b),
we mainly use the generation setting of Truth-
fulQA(Linetal.,2022). TheTruthfulQAdataset
contains818questions,whichareusedtoprompt
thetestedmodeltogenerateanswers. Weusethe
defaultQApromptformatwith6in-contextQA
16https://huggingface.co/tomh/toxigen_roberta

## Page 21

```
Model   MMLU  GSM8k  BBH   TydiQA Codex-Eval AlpacaEval ToxiGen TruthfulQA
        0-shot 8-shotCoT 3-shotCoT 1-shot Pass@10 %win %Toxic %Info+True
OLMo-7B 28.3   8.5   31.7   32.3  21.4    -    81.4   31.6
+SFT    47.3  15.5   36.9   35.2  28.6   57.0  14.4   41.2
+SFT+DPO 46.1 11.0   35.8   21.7  27.8   69.3   1.7   52.0
```

Table8: EvaluationofOLMo-7BmodelsbeforeandafterinstructionfinetuningandDPOtrainingonthefullTULU
evaluationsuite. LowerisbetterforToxiGenandhigherisbetterforothermetrics.

examples. Wefollowtheofficialscriptintheirof-
17
ficialimplemention todogreedydecodingand
answerpostprocessing. WetraintwoLLaMA2-
basedclassifiersforjudgingthetruthfulnessand
informativenessofthemodelresponse,duetothe
deprecationofGPT-3makingexactreplication
oftheoriginalTruthfulQAevaluationinfeasible.
WefindthattheLLaMA2judgesaregenerally
able to match the performance of the original
GPT-3-based judges used by Lin et al. (2022).
We report the rate of the responses being truth-
fulandinformative(%InformativeandTruthful)
followingTouvronetal.(2023b). Weonlyreport
the % Informative and Truthful as our primary
metric.
- AlpacaEval: WeusethepackageprovidedbyLi
etal.(2023),followingthedefaultsetupwhich
askstheevaluatedmodeltogenerateresponses
for805promptsandemployGPT-4tocompare
theresponsewithDavinci-003. Weemploythe
"alpaca_eval_gpt4"annotator. Weallowtheeval-
uatedmodeltogenerateupto2048tokens,with-
out specifying special stop sequences. The re-
portedwin-rateisthepercentageofmodelgener-
ationsthatGPT-4reportsasbeingpreferredover
thegenerationsfromDavinci-003.

17https://github.com/sylinrl/TruthfulQA/
