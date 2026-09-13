# arxiv-2305.18290v3.pdf

<!-- rendered by brewdoc from arxiv-2305.18290v3.pdf sha256 92cb3a2b71362acda98a789b03d88688fd33cf5fcf13f81d2b1de30ee7d3b67a -->

## Page 1

```
          Direct Preference Optimization:
Your  Language  Model  is Secretly a Reward  Model
```

RafaelRafailov?? ArchitSharma?? EricMitchell??

StefanoErmon?? ChristopherD.Manning? ChelseaFinn?

?StanfordUniversity?CZBiohub
{rafailov,architsh,eric.mitchell}@cs.stanford.edu

Abstract
Whilelarge-scaleunsupervisedlanguagemodels(LMs)learnbroadworldknowl-
edge and some reasoning skills, achieving precise control of their behavior is
difficult due to the completely unsupervised nature of their training. Existing
methodsforgainingsuchsteerabilitycollecthumanlabelsoftherelativequalityof
modelgenerationsandfine-tunetheunsupervisedLMtoalignwiththeseprefer-
ences,oftenwithreinforcementlearningfromhumanfeedback(RLHF).However,
RLHFisacomplexandoftenunstableprocedure,firstfittingarewardmodelthat
reflectsthehumanpreferences,andthenfine-tuningthelargeunsupervisedLM
usingreinforcementlearningtomaximizethisestimatedrewardwithoutdrifting
toofarfromtheoriginalmodel. Inthispaperweintroduceanewparameterization
oftherewardmodelinRLHFthatenablesextractionofthecorrespondingoptimal
policyinclosedform,allowingustosolvethestandardRLHFproblemwithonlya
simpleclassificationloss. Theresultingalgorithm,whichwecallDirectPrefer-
enceOptimization(DPO),isstable,performant,andcomputationallylightweight,
eliminatingtheneedfor samplingfromtheLMduringfine-tuningorperforming
significanthyperparametertuning. OurexperimentsshowthatDPOcanfine-tune
LMstoalignwithhumanpreferencesaswellasorbetterthanexistingmethods.
Notably,fine-tuningwithDPOexceedsPPO-basedRLHFinabilitytocontrolsen-
timentofgenerations,andmatchesorimprovesresponsequalityinsummarization
andsingle-turndialoguewhilebeingsubstantiallysimplertoimplementandtrain.

1 Introduction
Largeunsupervisedlanguagemodels(LMs)trainedonverylargedatasetsacquiresurprisingcapabili-
ties[11,7,42,8].However,thesemodelsaretrainedondatageneratedbyhumanswithawidevariety
ofgoals,priorities,andskillsets. Someofthesegoalsandskillsetsmaynotbedesirabletoimitate;for
example,whilewemaywantourAIcodingassistanttounderstandcommonprogrammingmistakes
inordertocorrectthem,nevertheless,whengeneratingcode,wewouldliketobiasourmodeltoward
the(potentiallyrare)high-qualitycodingabilitypresentinitstrainingdata. Similarly,wemightwant
ourlanguagemodeltobeawareofacommonmisconceptionbelievedby50%ofpeople,butwe
certainlydonotwantthemodeltoclaimthismisconceptiontobetruein50%ofqueriesaboutit!
Inotherwords,selectingthemodel'sdesiredresponsesandbehaviorfromitsverywideknowledge
andabilitiesiscrucialtobuildingAIsystemsthataresafe,performant,andcontrollable[28]. While
existingmethodstypicallysteerLMstomatchhumanpreferencesusingreinforcementlearning(RL),
?Equalcontribution;morejuniorauthorslistedearlier.
37thConferenceonNeuralInformationProcessingSystems(NeurIPS2023).
luJ
]GL.sc[
3v09281.5032:viXra

## Page 2

Figure1:DPOoptimizesforhumanpreferenceswhileavoidingreinforcementlearning.Existingmethods
forfine-tuninglanguagemodelswithhumanfeedbackfirstfitarewardmodeltoadatasetofpromptsand
humanpreferencesoverpairsofresponses,andthenuseRLtofindapolicythatmaximizesthelearnedreward.
Incontrast,DPOdirectlyoptimizesforthepolicybestsatisfyingthepreferenceswithasimpleclassification
objective,fittinganimplicitrewardmodelwhosecorrespondingoptimalpolicycanbeextractedinclosedform.
wewillshowthattheRL-basedobjectiveusedbyexistingmethodscanbeoptimizedexactlywitha
simplebinarycross-entropyobjective,greatlysimplifyingthepreferencelearningpipeline.
Atahighlevel,existingmethodsinstillthedesiredbehaviorsintoalanguagemodelusingcurated
setsofhumanpreferencesrepresentingthetypesofbehaviorsthathumansfindsafeandhelpful. This
preferencelearningstageoccursafteraninitialstageoflarge-scaleunsupervisedpre-trainingon
alargetextdataset. Whilethemoststraightforwardapproachtopreferencelearningissupervised
fine-tuningonhumandemonstrationsofhighqualityresponses,themostsuccessfulclassofmethods
isreinforcementlearningfromhuman(orAI)feedback(RLHF/RLAIF;[12,2]). RLHFmethodsfit
arewardmodeltoadatasetofhumanpreferencesandthenuseRLtooptimizealanguagemodel
policytoproduceresponsesassignedhighrewardwithoutdriftingexcessivelyfarfromtheoriginal
model. WhileRLHFproducesmodelswithimpressiveconversationalandcodingabilities,theRLHF
pipelineisconsiderablymorecomplexthansupervisedlearning,involvingtrainingmultipleLMsand
samplingfromtheLMpolicyintheloopoftraining,incurringsignificantcomputationalcosts.
Inthispaper,weshowhowtodirectlyoptimizealanguagemodeltoadheretohumanpreferences,
withoutexplicitrewardmodelingorreinforcementlearning. WeproposeDirectPreferenceOptimiza-
tion(DPO),analgorithmthatimplicitlyoptimizesthesameobjectiveasexistingRLHFalgorithms
(reward maximization with a KL-divergence constraint) but is simple to implement and straight-
forwardtotrain. Intuitively,theDPOupdateincreasestherelativelogprobabilityofpreferredto
dispreferredresponses,butitincorporatesadynamic,per-exampleimportanceweightthatprevents
themodeldegenerationthatwefindoccurswithanaiveprobabilityratioobjective. Likeexisting
algorithms,DPOreliesonatheoreticalpreferencemodel(suchastheBradley-Terrymodel;[5])that
measureshowwellagivenrewardfunctionalignswithempiricalpreferencedata. However,while
existingmethodsusethepreferencemodeltodefineapreferencelosstotrainarewardmodeland
thentrainapolicythatoptimizesthelearnedrewardmodel,DPOusesachangeofvariablestodefine
thepreferencelossasafunctionofthepolicydirectly. Givenadatasetofhumanpreferencesover
modelresponses,DPOcanthereforeoptimizeapolicyusingasimplebinarycrossentropyobjective,
producingtheoptimalpolicytoanimplicitrewardfunctionfittothepreferencedata.
Our main contribution is Direct Preference Optimization (DPO), a simple RL-free algorithm for
traininglanguagemodelsfrompreferences. OurexperimentsshowthatDPOisatleastaseffective
as existing methods, including PPO-based RLHF, for learning from preferences in tasks such as
sentimentmodulation,summarization,anddialogue,usinglanguagemodelswithupto6Bparameters.

2 RelatedWork
Self-supervisedlanguagemodelsofincreasingscalelearntocompletesometaskszero-shot[33]or
withfew-shotprompts[6,27,11]. However,theirperformanceondownstreamtasksandalignment
withuserintentcanbesignificantlyimprovedbyfine-tuningondatasetsofinstructionsandhuman-
writtencompletions[25,38,13,41]. This'instruction-tuning'procedureenablesLLMstogeneralize
toinstructionsoutsideoftheinstruction-tuningsetandgenerallyincreasetheirusability[13]. Despite
thesuccessofinstructiontuning,relativehumanjudgmentsofresponsequalityareofteneasierto
collectthanexpertdemonstrations,andthussubsequentworkshavefine-tunedLLMswithdatasetsof
humanpreferences,improvingproficiencyintranslation[20],summarization[40,51],story-telling
[51], and instruction-following [28, 34]. These methods first optimize a neural network reward
function for compatibility with the dataset of preferences under a preference model such as the

## Page 3

Bradley-Terry model [5], then fine-tune a language model to maximize the given reward using
reinforcement learning algorithms, commonly REINFORCE [47], proximal policy optimization
(PPO;[39]),orvariants[34].Aclosely-relatedlineofworkleveragesLLMsfine-tunedforinstruction
followingwithhumanfeedbacktogenerateadditionalsyntheticpreferencedatafortargetedattributes
such as safety or harmlessness [2], using only weak supervision from humans in the form of a
text rubric for the LLM's annotations. These methods represent a convergence of two bodies of
work: one body of work on training language models with reinforcement learning for a variety
ofobjectives[35,29,48]andanotherbodyofworkongeneralmethodsforlearningfromhuman
preferences [12, 21]. Despite the appeal of using relative human preferences, fine-tuning large
languagemodelswithreinforcementlearningremainsamajorpracticalchallenge;thisworkprovides
atheoretically-justifiedapproachtooptimizingrelativepreferenceswithoutRL.
Outsideofthecontextoflanguage,learningpoliciesfrompreferenceshasbeenstudiedinbothbandit
andreinforcementlearningsettings,andseveralapproacheshavebeenproposed. Contextualbandit
learning using preferences or rankings of actions, rather than rewards, is known as a contextual
duelingbandit(CDB;[50,14]). Intheabsenceofabsoluterewards,theoreticalanalysisofCDBs
substitutesthenotionofanoptimalpolicywithavonNeumannwinner,apolicywhoseexpectedwin
rateagainstanyotherpolicyisatleast50%[14]. However,intheCDBsetting,preferencelabels
aregivenonline,whileinlearningfromhumanpreferences,wetypicallylearnfromafixedbatchof
offlinepreference-annotatedactionpairs[49]. Similarly,preference-basedRL(PbRL)learnsfrom
binarypreferencesgeneratedbyanunknown'scoring'functionratherthanrewards[9,37]. Various
algorithmsforPbRLexist,includingmethodsthatcanreuseoff-policypreferencedata,butgenerally
involvefirstexplicitlyestimatingthelatentscoringfunction(i.e. therewardmodel)andsubsequently
optimizing it [16, 9, 12, 36, 21]. We instead present a single stage policy learning approach that
directlyoptimizesapolicytosatisfypreferences.

```
3  Preliminaries
WereviewtheRLHFpipelineinZiegleretal.(andlater[40,1,28]). Itusuallyincludesthreephases:
1)supervisedfine-tuning(SFT);2)preferencesamplingandrewardlearningand3)RLoptimization.
SFT:RLHFtypicallybeginsbyfine-tuningapre-trainedLMwithsupervisedlearningonhigh-quality
dataforthedownstreamtask(s)ofinterest(dialogue,summarization,etc.),toobtainamodel?SFT.
Reward Modelling Phase: In the second phase the SFT model is prompted with prompts x to
produce pairs of answers (y ,y ) ? ?SFT(y | x). These are then presented to human labelers
                1 2
who express preferences for one answer, denoted as y ? y | x where y and y denotes the
                               w   l       w    l
preferredanddispreferredcompletionamongst(y ,y )respectively. Thepreferencesareassumed
                            1 2
tobegeneratedbysomelatentrewardmodelr?(y,x),whichwedonothaveaccessto. Therearea
numberofapproachesusedtomodelpreferences,theBradley-Terry(BT)[5]modelbeingapopular
choice(althoughmoregeneralPlackett-Lucerankingmodels[32,23]arealsocompatiblewiththe
frameworkifwehaveaccesstoseveralrankedanswers). TheBTmodelstipulatesthatthehuman
preferencedistributionp?canbewrittenas:
                            exp(r?(x,y ))
             p?(y ?y |x)=          1      .          (1)
               1  2    exp(r?(x,y ))+exp(r?(x,y ))
                              1         2
AssumingaccesstoastaticdatasetofcomparisonsD =  x(i),y(i),y(i)N sampledfromp?,we
                                    w  l i=1
can parametrize a reward model r (x,y) and estimate the parameters via maximum likelihood.
                    ?
Framingtheproblemasabinaryclassificationwehavethenegativelog-likelihoodloss:
           L (r ,D)=?E      log?(r (x,y )?r (x,y ))  (2)
            R ?      (x,yw,yl)?D ? w   ?  l
where?isthelogisticfunction. InthecontextofLMs,thenetworkr (x,y)isofteninitializedfrom
                                      ?
theSFTmodel?SFT(y | x)withtheadditionofalinearlayerontopofthefinaltransformerlayer
thatproducesasinglescalarpredictionfortherewardvalue[51]. Toensurearewardfunctionwith
lowervariance,priorworksnormalizetherewards,suchthatE [r (x,y)]=0forallx.
                                   x,y?D ?
RL Fine-Tuning Phase: During the RL phase, the learned reward function is used to provide
feedbacktothelanguagemodel. Followingpriorworks[17,18],theoptimizationisformulatedas
          maxE        r (x,y)  ??D  ? (y |x)||? (y |x)  , (3)
          ??
             x?D,y???(y|x) ?  KL ?      ref
```

## Page 4

```
where? isaparametercontrollingthedeviationfromthebasereferencepolicy? ,namelytheini-
                                             ref
tial SFT model ?SFT. In practice, the language model policy ? is also initialized to ?SFT. The
                                    ?
added constraint is important, as it prevents the model from deviating too far from the distri-
bution on which the reward model is accurate, as well as maintaining the generation diversity
and preventing mode-collapse to single high-reward answers. Due to the discrete nature of lan-
guage generation, this objective is not differentiable and is typically optimized with reinforce-
ment learning. The standard approach [51, 40, 1, 28] has been to construct the reward function
r(x,y)=r (x,y)??(log? (y |x)?log? (y |x)),andmaximizeusingPPO[39].
       ?        ?        ref
4  DirectPreferenceOptimization
Motivatedbythechallengesofapplyingreinforcementlearningalgorithmsonlarge-scaleproblems
suchasfine-tuninglanguagemodels,ourgoalistoderiveasimpleapproachforpolicyoptimization
usingpreferencesdirectly. UnlikepriorRLHFmethods,whichlearnarewardandthenoptimizeit
viaRL,ourapproachleveragesaparticularchoiceofrewardmodelparameterizationthatenables
extractionofitsoptimalpolicyinclosedform,withoutanRLtrainingloop. Aswewilldescribe
nextindetail,ourkeyinsightistoleverageananalyticalmappingfromrewardfunctionstooptimal
policies,whichenablesustotransformalossfunctionoverrewardfunctionsintoalossfunction
overpolicies. Thischange-of-variablesapproachavoidsfittinganexplicit,standalonerewardmodel,
whilestilloptimizingunderexistingmodelsofhumanpreferences,suchastheBradley-Terrymodel.
Inessence,thepolicynetworkrepresentsboththelanguagemodelandthe(implicit)reward.
DerivingtheDPOobjective. WestartwiththesameRLobjectiveaspriorwork,Eq.3,undera
generalrewardfunctionr. Followingpriorwork[31,30,19,15],itisstraightforwardtoshowthat
theoptimalsolutiontotheKL-constrainedrewardmaximizationobjectiveinEq.3takestheform:
                       1          1
              ? (y |x)=  ? (y |x)exp r(x,y) ,        (4)
               r      Z(x) ref    ?
where Z(x) =  ? (y | x)exp 1r(x,y) is the partition function. See Appendix A.1 for a
           y ref     ?
completederivation. EvenifweusetheMLEestimater oftheground-truthrewardfunctionr?,itis
                              ?
stillexpensivetoestimatethepartitionfunctionZ(x)[19,15],whichmakesthisrepresentationhard
toutilizeinpractice. However,wecanrearrangeEq.4toexpresstherewardfunctionintermsofits
correspondingoptimalpolicy? ,thereferencepolicy? ,andtheunknownpartitionfunctionZ(-).
                 r             ref
Specifically,wefirsttakethelogarithmofbothsidesofEq.4andthenwithsomealgebraweobtain:
                         ? (y |x)
                r(x,y)=?log r  +?logZ(x).            (5)
                         ? (y |x)
                          ref
Wecanapplythisreparameterizationtotheground-truthrewardr?andcorrespondingoptimalmodel
??. Fortunately,theBradley-Terrymodeldependsonlyonthedifferenceofrewardsbetweentwo
completions,i.e.,p?(y ?y |x)=?(r?(x,y )?r?(x,y )). Substitutingthereparameterization
             1  2         1      2
in Eq. 5 for r?(x,y) into the preference model Eq. 1, the partition function cancels, and we can
expressthehumanpreferenceprobabilityintermsofonlytheoptimalpolicy??andreferencepolicy
? . Thus,theoptimalRLHFpolicy??undertheBradley-Terrymodelsatisfiesthepreferencemodel:
 ref
          p?(y ?y |x)=                               (6)
             1  2
                     1+exp ?log ??(y2|x) ??log ??(y1|x)
                              ?ref(y2|x) ?ref(y1|x)
The derivation is in Appendix A.2. While Eq. 6 uses the Bradley-Terry model, we can similarly
deriveexpressionsunderthemoregeneralPlackett-Lucemodels[32,23],showninAppendixA.3.
Nowthatwehavetheprobabilityofhumanpreferencedataintermsoftheoptimalpolicyratherthan
therewardmodel,wecanformulateamaximumlikelihoodobjectiveforaparametrizedpolicy? .
                                                      ?
Analogoustotherewardmodelingapproach(i.e. Eq.2),ourpolicyobjectivebecomes:
                               ? (y |x)   ? (y |x)
    L  (? ;? )=?E      log? ?log ? w  ??log ? l   .  (7)
     DPO ? ref  (x,yw,yl)?D    ? (y |x)   ? (y |x)
                               ref w       ref l
Thisway,wefitanimplicitrewardusinganalternativeparameterization,whoseoptimalpolicyis
simply? . Moreover,sinceourprocedureisequivalenttofittingareparametrizedBradley-Terry
     ?
```

## Page 5

```
model,itenjoyscertaintheoreticalproperties,suchasconsistenciesundersuitableassumptionofthe
preferencedatadistribution[4]. InSection5,wefurtherdiscusstheoreticalpropertiesofDPOin
relationtootherworks.
WhatdoestheDPOupdatedo? ForamechanisticunderstandingofDPO,itisusefultoanalyzethe
gradientofthelossfunctionL . Thegradientwithrespecttotheparameters?canbewrittenas:
                 DPO
  ? L (? ;? )=
   ? DPO ? ref
 ??E         ?(r? (x,y )?r? (x,y )) ? log?(y |x) ? ? log?(y |x) ,
    (x,yw,yl)?D ?  l  ?  w      ?    w     ?    l
            higherweightwhenrewardestimateiswrong increaselikelihoodofyw decreaselikelihoodofyl
wherer? (x,y)=?log ??(y|x) istherewardimplicitlydefinedbythelanguagemodel? andrefer-
    ?        ?ref(y|x)                           ?
encemodel? (moreinSection5). Intuitively,thegradientofthelossfunctionL increasesthe
        ref                                  DPO
likelihoodofthepreferredcompletionsy anddecreasesthelikelihoodofdispreferredcompletions
                       w
y . Importantly,theexamplesareweighedbyhowmuchhighertheimplicitrewardmodelr? rates
 l                                                 ?
the dispreferred completions, scaled by ?, i.e, how incorrectly the implicit reward model orders
the completions, accounting for the strength of the KL constraint. Our experiments suggest the
importanceofthisweighting,asanaiveversionofthismethodwithouttheweightingcoefficientcan
causethelanguagemodeltodegenerate(AppendixTable3).
DPOoutline. ThegeneralDPOpipelineisasfollows: 1)Samplecompletionsy ,y ?? (-|x)
                                             1 2  ref
for every prompt x, label with human preferences to construct the offline dataset of preferences
D ={x(i),y(i),y )(i)}N and2)optimizethelanguagemodel? tominimizeL forthegiven
       w  l  i=1                    ?        DPO
? andDanddesired?. Inpractice,onewouldliketoreusepreferencedatasetspubliclyavailable,
 ref
rather than generating samples and gathering human preferences. Since the preference datasets
are sampled using ?SFT, we initialize ? = ?SFT whenever available. However, when ?SFT is
                       ref
not available, we initialize ? by maximizing likelihood of preferred completions (x,y ), that
                 ref                              w
is, ? =argmax E  [log?(y |x)]. This procedure helps mitigate the distribution shift
   ref    ? x,yw?D   w
betweenthetruereferencedistributionwhichisunavailable,and? usedbyDPO.Furtherdetails
                                     ref
relatedtotheimplementationandhyperparameterscanbefoundinAppendixB.
```

5 TheoreticalAnalysisofDPO
Inthissection,wegivefurtherinterpretationoftheDPOmethod,providetheoreticalbacking,and
relateadvantagesofDPOtoissueswithactorcriticalgorithmsusedforRLHF(suchasPPO[39]).
5.1 YourLanguageModelIsSecretlyaRewardModel
DPOisabletobypassbothfittinganexplicitrewardandperformingRLtolearnthepolicyusing
asinglemaximumlikelihoodobjective. NotetheoptimizationobjectiveEq. 5isequivalenttoa
Bradley-Terrymodelwitharewardparameterizationr?(x,y)=?log ? ? ?(y|x) andweoptimizeour
?ref(y|x)
parametricmodel? ,equivalentlytotherewardmodeloptimizationinEq. 2underthechangeof
?
variables. Inthissectionwewillbuildthetheorybehindthisreparameterization,showthatitdoes
notconstraintheclassoflearnedrewardmodels,andallowsfortheexactrecoveryoftheoptimal
policy. Webeginwithbydefininganequivalencerelationbetweenrewardfunctions.
Definition 1. We say that two reward functions r(x,y) and r'(x,y) are equivalent iff
r(x,y)?r'(x,y)=f(x)forsomefunctionf.
Itiseasytoseethatthisisindeedanequivalencerelation,whichpartitionsthesetofrewardfunctions
intoclasses. Wecanstatethefollowingtwolemmas:
Lemma1. UnderthePlackett-Luce,andinparticulartheBradley-Terry,preferenceframework,two
rewardfunctionsfromthesameclassinducethesamepreferencedistribution.
Lemma2. Tworewardfunctionsfromthesameequivalenceclassinducethesameoptimalpolicy
undertheconstrainedRLproblem.
TheproofsarestraightforwardandwedeferthemtoAppendixA.5. Thefirstlemmaisawell-known
under-specificationissuewiththePlackett-Lucefamilyofmodels[32].Duetothisunder-specification,

## Page 6

```
weusuallyhavetoimposeadditionalidentifiabilityconstraintstoachieveanyguaranteesontheMLE
estimatesfromEq. 2[4]. Thesecondlemmastatesthatallrewardfunctionsfromthesameclass
yieldthesameoptimalpolicy,henceforourfinalobjective,weareonlyinterestedinrecoveringan
arbitraryrewardfunctionfromtheoptimalclass. WeprovethefollowingTheoreminAppendixA.6:
Theorem 1. Under mild assumptions, all reward classes consistent with the Plackett-Luce
(and Bradley-Terry in particular) models can be represented with the reparameterization
r(x,y)=?log ?(y|x) forsomemodel?(y |x)andagivenreferencemodel? (y |x).
         ?ref(y|x)                          ref
ProofSketch. Consideranyrewardfunctionr(x,y),whichinducesacorrespondingoptimalmodel
? (y |x),specifiedbyEq. 4. Wewillshowthatarewardfunctionfromtheequivalenceclassofr
 r
canberepresentedusingthereparameterizationgivenabove. Wedefinetheprojectionf as
         f(r;? ,?)(x,y)=r(x,y)??log ? (y |x)exp r(x,y) (8)
            ref                 ref     ?
                              y
Theoperatorf simplynormalizestherewardfunctionwiththelogarithmofthepartitionfunction
of? . Sincetheaddednormalizationtermisonlyafunctionoftheprefixx,f(r;? ,?)(x,y)isa
   r                                          ref
rewardfunctionintheequivalenceclassofr(x,y). Finally,replacingrwiththeRHSofEq.5(which
holdsforanyrewardfunction),wehavef(r;? ,?)(x,y)=?log ?r(y|x). Thatis,theprojection
                          ref         ?ref(y|x)
f producesamemberoftheequivalenceclassofrwiththedesiredform,andwedonotloseany
generalityinourrewardmodelfromtheproposedreparameterization.
We can alternatively view Theorem 1 as specifying exactly which reward function within each
equivalenceclasstheDPOreparameterizationselects,thatis,therewardfunctionsatisfying:
                    ? (y |x)exp r(x,y) =1,           (9)
                    ref      ?
                  y
                     =?(y|x),usingThm.1reparam.
i.e.,?(y | x)isavaliddistribution(probabilitiesarepositiveandsumto1). However,following
Eq.4,wecanseethatEq.9isthepartitionfunctionoftheoptimalpolicyinducedbythereward
functionr(x,y). ThekeyinsightoftheDPOalgorithmisthatwecanimposecertainconstraintson
theunder-constrainedPlackett-Luce(andBradley-Terryinparticular)familyofpreferencemodels,
suchthatwepreservetheclassofrepresentablerewardmodels,butexplicitlymaketheoptimalpolicy
inEq. 4analyticallytractableforallpromptsx.
5.2 InstabilityofActor-CriticAlgorithms
Wecanalsouseourframeworktodiagnoseinstabilitieswithstandardactor-criticalgorithmsused
for the RLHF, such as PPO. We follow the RLHF pipeline and focus on the RL fine-tuning step
outlinedinSection3. Wecandrawconnectionstothecontrolasinferenceframework[22]forthe
constrainedRLproblemoutlinedin3. Weassumeaparameterizedmodel? (y |x)andminimize
                                           ?
D  [? (y|x)||??(y |x)]where??istheoptimalpolicyfromEq. 7inducedbytherewardfunction
 KL ?
r (y,x). Withsomealgebrathisleadstotheoptimizationobjective:
 ?
   maxE    r (x,y)??log  ? (y |x)exp 1 r (x,y) ??log ? ? (y |x) (10)
   ??  ??(y|x) ?     y ref      ? ?        ? ref (y |x)
                 KL
                      f(r?,?ref,?)
Thisisthesameobjectiveoptimizedinpriorworks[51,40,1,28]usingtheDPO-equivalentreward
fortherewardclassofr . Inthissetting,wecaninterpretthenormalizationterminf(r ,? ,?)
              ?                                   ? ref
asthesoftvaluefunctionofthereferencepolicy? . Whilethistermdoesnotaffecttheoptimal
                             ref
solution,withoutit,thepolicygradientoftheobjectivecouldhavehighvariance,makinglearning
unstable. Wecanaccommodateforthenormalizationtermusingalearnedvaluefunction,butthat
canalsobedifficulttooptimize. Alternatively,priorworkshavenormalizedrewardsusingahuman
completionbaseline,essentiallyasinglesampleMonte-Carloestimateofthenormalizingterm. In
contrasttheDPOreparameterizationyieldsarewardfunctionthatdoesnotrequireanybaselines.
```

## Page 7

| 0000001 ....... 4567890 0.0 2.5 5.0 7.5KL(10.0 ref1)2.5 15.0 17.5 20.0 draweR IMDb Sentiment Generation DUPPnPOlOik (e(OOliuhuror siom)dpl.) PPPrPPeOOfe--GGrrTTe d((OT-FRuTLr )impl.) |  |  |  | 00000000 ........ 01234567 TL;DR SummarizaDPtPPOiOon WinPSrF eTRferaretde-FT vs ReGBfePesTtr- Joef n12c8e etar niW 0.00 0.2S5ampling 0t.e50mperatur0e.75 1.00 |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| raweR 0.7 0.6 0.5 0.4 | 0.0 2.5 5.0 | 7.5 10.0 12.5 15.0 17.5 20.0 DPO (Ours) PPO-GT (Our impl.) Unlikelihood PPO-GT (TRL) PPO (Our impl.) Preferred-FT |  | 00000 ..... 01234 tar niW | 0.00 0.25 |  | 0.50 0.75 |  | 1.00 |
|  |  | KL( | ref) |  |  | Sa | mpling te | mperature |  |

Figure2:Left.ThefrontierofexpectedrewardvsKLtothereferencepolicy.DPOprovidesthehighestexpected
rewardforallKLvalues,demonstratingthequalityoftheoptimization. Right. TL;DRsummarizationwin
ratesvs.human-writtensummaries,usingGPT-4asevaluator.DPOexceedsPPO'sbest-caseperformanceon
summarization,whilebeingmorerobusttochangesinthesamplingtemperature.
6 Experiments
Inthissection,weempiricallyevaluateDPO'sabilitytotrainpoliciesdirectlyfrompreferences. First,
inawell-controlledtext-generationsetting,weask: howefficientlydoesDPOtradeoffmaximizing
rewardandminimizingKL-divergencewiththereferencepolicy,comparedtocommonpreference
learningalgorithmssuchasPPO?Next,weevaluateDPO'sperformanceonlargermodelsandmore
difficultRLHFtasks,includingsummarizationanddialogue. Wefindthatwithalmostnotuning
ofhyperparameters,DPOtendstoperformaswellorbetterthanstrongbaselineslikeRLHFwith
PPOaswellasreturningthebestofN sampledtrajectoriesunderalearnedrewardfunction. Before
presentingtheseresults,wedescribetheexperimentalset-up;additionaldetailsareinAppendixC.
Tasks. Ourexperimentsexplorethreedifferentopen-endedtextgenerationtasks. Forallexperiments,
algorithms learn a policy from a dataset of preferences D =
x(i),y(i),y(i)N
. In controlled
w l i=1
sentimentgeneration,xisaprefixofamoviereviewfromtheIMDbdataset[24],andthepolicy
must generate y with positive sentiment. In order to perform a controlled evaluation, for this
experimentwegeneratepreferencepairsovergenerationsusingapre-trainedsentimentclassifier,
wherep(positive|x,y )>p(positive|x,y ). ForSFT,wefine-tuneGPT-2-largeuntilconvergence
w l
onreviewsfromthetrainsplitoftheIMDBdataset(furtherdetailsinAppC.1). Insummarization,
x is a forum post from Reddit; the policy must generate a summary y of the main points in the
post. Followingpriorwork,weusetheRedditTL;DRsummarizationdataset[43]alongwithhuman
preferencesgatheredbyStiennonetal.. WeuseanSFTmodelfine-tunedonhuman-writtenforum
post summaries2 with the TRLX [44] framework for RLHF. The human preference dataset was
gatheredbyStiennonetal.onsamplesfromadifferent,butsimilarly-trained,SFTmodel. Finally,in
single-turndialogue,xisahumanquery,whichmaybeanythingfromaquestionaboutastrophysics
toarequestforrelationshipadvice. Apolicymustproduceanengagingandhelpfulresponseyto
auser'squery;weusetheAnthropicHelpfulandHarmlessdialoguedataset[1],containing170k
dialoguesbetweenahumanandanautomatedassistant. Eachtranscriptendswithapairofresponses
generatedbyalarge(althoughunknown)languagemodelalongwithapreferencelabeldenoting
thehuman-preferredresponse. Inthissetting,nopre-trainedSFTmodelisavailable;wetherefore
fine-tuneanoff-the-shelflanguagemodelononlythepreferredcompletionstoformtheSFTmodel.
Evaluation. Ourexperimentsusetwodifferentapproachestoevaluation. Inordertoanalyzethe
effectivenessofeachalgorithminoptimizingtheconstrainedrewardmaximizationobjective,inthe
controlledsentimentgenerationsettingweevaluateeachalgorithmbyitsfrontierofachievedreward
andKL-divergencefromthereferencepolicy;thisfrontieriscomputablebecausewehaveacccessto
theground-truthrewardfunction(asentimentclassifier). However,intherealworld,thegroundtruth
rewardfunctionisnotknown;therefore,weevaluatealgorithmswiththeirwinrateagainstabaseline
policy,usingGPT-4asaproxyforhumanevaluationofsummaryqualityandresponsehelpfulness
in the summarization and single-turn dialogue settings, respectively. For summarization, we use
referencesummariesinthetestsetasthebaseline;fordialogue,weusethepreferredresponseinthe
2https://huggingface.co/CarperAI/openai_summarize_tldr_sft

## Page 8

| 000000 ...... 123456 etar niW Anthropic-HH Dialogue Win Rate vs Chosen 0.25 Sa0m.50pling temper0a.7t5ure 1.00 DBePsOt of 128 PPryethfeiarr-e2d.8-FBT |  |  |  | 000000000 ......... 334455667 050505050 Dialogue Win Rate Evolution etar niW 0 300 600 900F1i2n00e-1t5u00ni1n8g00 s2t1e00p2400270030003300 DPO (temp = 1.0) DPO (temp = 0.7) |  |  |  |  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  | Dialo | gue | Wi | n R | ate E | volu | tion |  |  |
| 0.6 |  |  |  | 0.70 0.65 0.60 |  |  |  |  |  |  |  |  |  |  |
| 0.5 etar 0.4 ni |  |  |  | 0.55 etar 0.50 n |  |  |  |  |  |  |  |  |  |  |
| W |  |  |  | iW |  |  |  |  |  |  |  |  |  |  |
| 0.3 |  |  |  | 0.45 |  |  |  |  |  |  |  |  |  |  |
| 0.2 |  | DPO Best of 128 | Preferred-FT Pythia-2.8B | 0.40 0.35 |  |  | DPO | (tem | p = 1 | .0) | DPO ( | temp | = 0. | 7) |
| 0.1 | 0.25 0.50 0.75 Sampling temperature |  | 1.00 | 0.30 | 0 300 600 |  | 900 12001500180021002400270030003300 Fine-tuning step |  |  |  |  |  |  |  |

Figure3:Left.WinratescomputedbyGPT-4forAnthropic-HHone-stepdialogue;DPOistheonlymethod
thatimprovesoverchosensummariesintheAnthropic-HHtestset. Right. Winratesfordifferentsampling
temperaturesoverthecourseoftraining. DPO'simprovementoverthedatasetlabelsisfairlystableoverthe
courseoftrainingfordifferentsamplingtemperatures.
testdatasetasthebaseline. WhileexistingstudiessuggestLMscanbebetterautomatedevaluators
thanexistingmetrics[10],weconductahumanstudytojustifyourusageofGPT-4forevaluation
inSec.6.4. WefindGPT-4judgmentscorrelatestronglywithhumans,withhumanagreementwith
GPT-4typicallysimilarorhigherthaninter-humanannotatoragreement.
Methods. InadditiontoDPO,weevaluateseveralexistingapproachestotraininglanguagemodels
toadheretohumanpreferences. Mostsimply,weexplorezero-shotpromptingwithGPT-J[45]in
thesummarizationtaskand2-shotpromptingwithPythia-2.8B[3]inthedialoguetask. Inaddition,
weevaluatetheSFTmodelaswellasPreferred-FT,whichisamodelfine-tunedwithsupervised
learning on the chosen completion y from either the SFT model (in controlled sentiment and
w
summarization)oragenericLM(insingle-turndialogue). Anotherpseudo-supervisedmethodis
Unlikelihood[46],whichsimplyoptimizesthepolicytomaximizetheprobabilityassignedtoy and
w
minimizetheprobabilityassignedtoy ;weuseanoptionalcoefficient??[0,1]onthe'unlikelihood'
l
term. We also consider PPO [39] using a reward function learned from the preference data and
PPO-GT, which is an oracle that learns from the ground truth reward function available in the
controlledsentimentsetting. Inoursentimentexperiments,weusetwoimplementationsofPPO-GT,
oneof-the-shelfversion[44]aswellasamodifiedversionthatnormalizesrewardsandfurthertunes
hyperparameterstoimproveperformance(wealsousethesemodificationswhenrunning'normal'
PPOwithlearnedrewards). Finally,weconsidertheBestofN baseline,samplingN responsesfrom
theSFTmodel(orPreferred-FTindialogue)andreturningthehighest-scoringresponseaccordingto
arewardfunctionlearnedfromthepreferencedataset. Thishigh-performingmethoddecouplesthe
qualityoftherewardmodelfromthePPOoptimization,butiscomputationallyimpracticalevenfor
moderateN asitrequiressamplingN completionsforeveryqueryattesttime.

6.1 HowwellcanDPOoptimizetheRLHFobjective?
The KL-constrained reward maximization objective used in typical RLHF algorithms balances
exploitation of reward while restricting the policy from deviating far from the reference policy.
Therefore,whencomparingalgorithms,wemusttakeintoaccountbothrewardachievedaswellasthe
KLdiscrepancy;achievingslightlyhigherrewardbutwithmuchhigherKLisnotnecessarilydesirable.
Figure2showsthereward-KLfrontierforvariousalgorithmsinthesentimentsetting. Weexecute
multipletrainingrunsforeachalgorithm,usingadifferenthyperparameterforpolicyconservativeness
in each run (target KL ? {3,6,9,12} for PPO, ? ? {0.05,0.1,1,5}, ? ? {0.05,0.1,0.5,1} for
unlikelihood, random seeds for preferred-FT). This sweep includes 22 runs in total. After each
100trainingstepsuntilconvergence,weevaluateeachpolicyonasetoftestprompts,computing
theaveragerewardunderthetruerewardfunctionaswellastheaveragesequence-levelKL3 with
the reference policy KL(? ||? ). We find that DPO produces by far the most efficient frontier,
ref
achieving the highest reward while still achieving low KL. This result is particularly notable for
multiplereasons. First,DPOandPPOoptimizethesameobjective,butDPOisnotablymoreefficient;
3Thatis,thesumoftheper-timestepKL-divergences.

## Page 9

DPO'sreward/KLtradeoffstrictlydominatesPPO.Second,DPOachievesabetterfrontierthanPPO,
evenwhenPPOcanaccessgroundtruthrewards(PPO-GT).

6.2 CanDPOscaletorealpreferencedatasets?
Next,weevaluatefine-tuningperformanceofDPOonsummarizationandsingle-turndialogue. For
summarization,automaticevaluationmetricssuchasROUGEcanbepoorlycorrelatedwithhuman
preferences[40],andpriorworkhasfoundthatfine-tuningLMsusingPPOonhumanpreferences
toprovidemoreeffectivesummaries. Weevaluatedifferentmethodsbysamplingcompletionson
thetestsplitofTL;DRsummarizationdataset,andcomputingtheaveragewinrateagainstreference
completionsinthetestset. Thecompletionsforallmethodsaresampledattemperaturesvarying
from0.0to1.0,andthewinratesareshowninFigure2(right). DPO,PPOandPreferred-FTall
fine-tunethesameGPT-JSFTmodel4. WefindthatDPOhasawinrateofapproximately61%ata
temperatureof0.0,exceedingtheperformanceofPPOat 57%atitsoptimalsamplingtemperature
of 0.0. DPO also achieves a higher maximum win rate compared to the best of N baseline. We
notethatwedidnotmeaningfullytuneDPO's? hyperparameter,sotheseresultsmayunderestimate
DPO'spotential. Moreover,wefindDPOtobemuchmorerobusttothesamplingtemperaturethan
PPO,theperformanceofwhichcandegradetothatofthebaseGPT-Jmodelathightemperatures.
Preferred-FTdoesnotimprovesignificantlyovertheSFTmodel. WealsocompareDPOandPPO
head-to-headinhumanevaluationsinSection6.4,whereDPOsamplesattemperature0.25were
preferred58%timesoverPPOsamplesattemperature0.
On single-turn dialogue, we evaluate the different methods on the subset of the test split of the
AnthropicHHdataset[1]withonestepofhuman-assistantinteraction. GPT-4evaluationsusethe
preferredcompletionsonthetestasthereferencetocomputethewinratefordifferentmethods. As
thereisnostandardSFTmodelforthistask,westartwithapre-trainedPythia-2.8B,usePreferred-FT
totrainareferencemodelonthechosencompletionssuchthatcompletionsarewithindistribution
of the model, and then train using DPO. We also compare against the best of 128 Preferred-FT
completions(wefoundtheBestofN baselineplateausat128completionsforthistask;seeAppendix
Figure4)anda2-shotpromptedversionofthePythia-2.8Bbasemodel,findingDPOperformsas
wellorbetterforthebest-performingtemperaturesforeachmethod. WealsoevaluateanRLHF
modeltrainedwithPPOontheAnthropicHHdataset5fromawell-knownsource6,butareunable
tofindapromptorsamplingtemperaturethatgivesperformancebetterthanthebasePythia-2.8B
model. BasedonourresultsfromTL;DRandthefactthatbothmethodsoptimizethesamereward
function,weconsiderBestof128aroughproxyforPPO-levelperformance. Overall,DPOisthe
onlycomputationallyefficientmethodthatimprovesoverthepreferredcompletionsintheAnthropic
HHdataset,andprovidessimilarorbetterperformancetothecomputationallydemandingBestof
128baseline. Finally,Figure3showsthatDPOconvergestoitsbestperformancerelativelyquickly.

```
6.3 Generalizationtoanewinputdistribution
                                       Winratevs.groundtruth
TofurthercomparetheperformanceofPPOandDPOun-
                                   Alg. Temp0 Temp0.25
derdistributionshifts,weevaluatethePPOandDPOpoli-
ciesfromourRedditTL;DRsummarizationexperimenton DPO 0.36 0.31
                                   PPO  0.26    0.23
adifferentdistribution,newsarticlesinthetestsplitofthe
CNN/DailyMaildataset[26],usingthebestsamplingtemper-
                                  Table 1: GPT-4 win rates vs. ground
aturesfromTL;DR(0and0.25). Theresultsarepresented
                                  truth summaries for out-of-distribution
in Table 1. We computed the GPT-4 win rate against the CNN/DailyMailinputarticles.
ground-truthsummariesinthedatasets,usingthesameGPT-
4(C)promptweusedforRedditTL;DR,butreplacingthewords"forumpost"with"newsarticle".
Forthisnewdistribution,DPOcontinuestooutperformthePPOpolicybyasignificantmargin. This
experimentprovidesinitialevidencethatDPOpoliciescangeneralizesimilarlywelltoPPOpolicies,
eventhoughDPOdoesnotusetheadditionalunlabeledRedditTL;DRpromptsthatPPOuses.
```

4https://huggingface.co/CarperAI/openai_summarize_tldr_sft
5https://huggingface.co/reciprocate/ppo_hh_pythia-6B
6https://github.com/CarperAI/trlx/tree/main/examples/hh

## Page 10

```
6.4 ValidatingGPT-4judgmentswithhumanjudgments
We conduct a human study to verify the reliability of GPT-4's judgments, using the results of
the TL;DR summarization experiment and two different GPT-4 prompts. The GPT-4 (S) (sim-
ple) promptsimply asks forwhich summary better-summarizes theimportant information inthe
post. The GPT-4 (C) (concise) prompt also asks for which summary is more concise; we eval-
uate this prompt because we find that GPT-4 prefers longer, more repetitive summaries than hu-
mans do with the GPT-4 (S) prompt. See Appendix C.2 for the complete prompts. We perform
three comparisons, using the highest (DPO, temp. 0.25), the lowest (PPO, temp. 1.0), and a
middle-performing(SFT,temp. 0.25)methodwith
theaimofcoveringadiversityofsamplequalities; DPO SFT PPO-1
all three methods are compared against greedily-
                              Nrespondents 272 122 199
sampled PPO (its best-performing temperature).
We find that with both prompts, GPT-4 tends to GPT-4(S)win% 47 27 13
agreewithhumansaboutasoftenashumansagree GPT-4(C)win% 54 32 12
witheachother,suggestingthatGPT-4isareason- Humanwin% 58 43 17
ableproxyforhumanevaluations(duetolimited GPT-4(S)-Hagree 70 77 86
humanraters,weonlycollectmultiplehumanjudg- GPT-4(C)-Hagree 67 79 85
mentsfortheDPOandPPO-1comparisons). Over- H-Hagree 65 - 87
all,theGPT-4(C)promptgenerallyprovideswin
                             Table 2: Comparing human and GPT-4 win rates
ratesmorerepresentativeofhumans;wetherefore
                             andper-judgmentagreementonTL;DRsummariza-
usethispromptforthemainresultsinSection6.2.
                             tionsamples.HumansagreewithGPT-4aboutas
For additional details about the human study, in- muchastheyagreewitheachother.Eachexperi-
cludingthewebinterfacepresentedtoratersand mentcomparesasummaryfromthestatedmethod
thelistofhumanvolunteers,seeAppendixD.3. withasummaryfromPPOwithtemperature0.
```

7 Discussion
Learningfrompreferencesisapowerful,scalableframeworkfortrainingcapable,alignedlanguage
models. WehaveintroducedDPO,asimpletrainingparadigmfortraininglanguagemodelsfrom
preferenceswithoutreinforcementlearning. Ratherthancoercingthepreferencelearningproblem
intoastandardRLsettinginordertouseoff-the-shelfRLalgorithms,DPOidentifiesamapping
betweenlanguagemodelpoliciesandrewardfunctionsthatenablestrainingalanguagemodelto
satisfyhumanpreferencesdirectly,withasimplecross-entropyloss,withoutreinforcementlearning
orlossofgenerality. Withvirtuallynotuningofhyperparameters,DPOperformssimilarlyorbetter
thanexistingRLHFalgorithms,includingthosebasedonPPO;DPOthusmeaningfullyreducesthe
barriertotrainingmorelanguagemodelsfromhumanpreferences.
Limitations&FutureWork. Ourresultsraiseseveralimportantquestionsforfuturework. How
doestheDPOpolicygeneralizeoutofdistribution,comparedwithlearningfromanexplicitreward
function? OurinitialresultssuggestthatDPOpoliciescangeneralizesimilarlytoPPO-basedmodels,
but more comprehensive study is needed. For example, can training with self-labeling from the
DPOpolicysimilarlymakeeffectiveuseofunlabeledprompts? Onanotherfront,howdoesreward
over-optimizationmanifestinthedirectpreferenceoptimizationsetting,andistheslightdecrease
inperformanceinFigure3-rightaninstanceofit? Additionally,whileweevaluatemodelsupto
6Bparameters,explorationofscalingDPOtostate-of-the-artmodelsordersofmagnitudelargeris
anexcitingdirectionforfuturework. Regardingevaluations,wefindthatthewinratescomputed
byGPT-4areimpactedbytheprompt; futureworkmaystudythebestwaytoelicithigh-quality
judgmentsfromautomatedsystems.Finally,manypossibleapplicationsofDPOexistbeyondtraining
languagemodelsfromhumanpreferences,includingtraininggenerativemodelsinothermodalities.

Acknowledgements
EMgratefullyacknowledgesfundingfromaKnight-HennessyGraduateFellowship. CFandCM
are CIFAR Fellows. This work was supported in part by the Stanford Accelerator for Learning
(SAL)andStanfordInstituteforHuman-CenteredArtificialIntelligence(HAI)GenerativeAIforthe
FutureofLearningseedgrantprogram. TheStanfordCenterforResearchonFoundationModels
(CRFM)providedpartofthecomputeresourcesusedfortheexperimentsinthiswork. Thiswork
wassupportedinpartbyONRgrantN00014-20-1-2675.

## Page 11

References
[1] Y.Bai,A.Jones,K.Ndousse,A.Askell,A.Chen,N.DasSarma,D.Drain,S.Fort,D.Ganguli,
T.Henighan,N.Joseph,S.Kadavath,J.Kernion,T.Conerly,S.El-Showk,N.Elhage,Z.Hatfield-
Dodds, D. Hernandez, T. Hume, S. Johnston, S. Kravec, L. Lovitt, N. Nanda, C. Olsson,
D.Amodei,T.Brown,J.Clark,S.McCandlish,C.Olah,B.Mann,andJ.Kaplan. Traininga
helpfulandharmlessassistantwithreinforcementlearningfromhumanfeedback,2022.
[2] Y.Bai,S.Kadavath,S.Kundu,A.Askell,J.Kernion,A.Jones,A.Chen,A.Goldie,A.Mirho-
seini,C.McKinnon,C.Chen,C.Olsson,C.Olah,D.Hernandez,D.Drain,D.Ganguli,D.Li,
E.Tran-Johnson,E.Perez,J.Kerr,J.Mueller,J.Ladish,J.Landau,K.Ndousse,K.Lukosuite,
L.Lovitt,M.Sellitto,N.Elhage,N.Schiefer,N.Mercado,N.DasSarma,R.Lasenby,R.Lar-
son,S.Ringer,S.Johnston,S.Kravec,S.E.Showk,S.Fort,T.Lanham,T.Telleen-Lawton,
T.Conerly,T.Henighan,T.Hume,S.R.Bowman,Z.Hatfield-Dodds,B.Mann,D.Amodei,
N.Joseph,S.McCandlish,T.Brown,andJ.Kaplan. Constitutionalai: Harmlessnessfromai
feedback,2022.
[3] S.Biderman,H.Schoelkopf,Q.Anthony,H.Bradley,K.O'Brien,E.Hallahan,M.A.Khan,
S.Purohit,U.S.Prashanth,E.Raff,A.Skowron,L.Sutawika,andO.vanderWal. Pythia: A
suiteforanalyzinglargelanguagemodelsacrosstrainingandscaling,2023.
[4] H.BongandA.Rinaldo. GeneralizedresultsfortheexistenceandconsistencyoftheMLE
in the Bradley-Terry-Luce model. International Conference on Machine Learning, 2022.
arXiv:2110.11487.
[5] R.A.BradleyandM.E.Terry. Rankanalysisofincompleteblockdesigns: I.themethodof
pairedcomparisons. Biometrika,39(3/4):324-345,1952. doi: https://doi.org/10.2307/2334029.
[6] T. Brown, B. Mann, N. Ryder, M. Subbiah, J. D. Kaplan, P. Dhariwal, A. Neelakantan,
P.Shyam,G.Sastry,A.Askell,S.Agarwal,A.Herbert-Voss,G.Krueger,T.Henighan,R.Child,
A.Ramesh,D.Ziegler,J.Wu,C.Winter,C.Hesse,M.Chen,E.Sigler,M.Litwin,S.Gray,
B.Chess,J.Clark,C.Berner,S.McCandlish,A.Radford,I.Sutskever,andD.Amodei. Lan-
guagemodelsarefew-shotlearners. InH.Larochelle,M.Ranzato,R.Hadsell,M.Balcan,and
H.Lin,editors,AdvancesinNeuralInformationProcessingSystems,volume33,pages1877-
1901. Curran Associates, Inc., 2020. URL https://proceedings.neurips.cc/paper_
files/paper/2020/file/1457c0d6bfcb4967418bfb8ac142f64a-Paper.pdf.
[7] T.Brown,B.Mann,N.Ryder,M.Subbiah,J.D.Kaplan,P.Dhariwal,A.Neelakantan,P.Shyam,
G. Sastry, A. Askell, et al. Language models are few-shot learners. Advances in neural
informationprocessingsystems,33:1877-1901,2020.
[8] S.Bubeck,V.Chandrasekaran,R.Eldan,J.Gehrke,E.Horvitz,E.Kamar,P.Lee,Y.T.Lee,
Y.Li,S.Lundberg,H.Nori,H.Palangi,M.T.Ribeiro,andY.Zhang. Sparksofartificialgeneral
intelligence: EarlyexperimentswithGPT-4,2023. arXivpreprintarXiv:2303.12712.
[9] R. Busa-Fekete, B. Szorenyi, P. Weng, W. Cheng, and E. Hullermeier. Preference-based
reinforcement learning: evolutionary direct policy search using a preference-based racing
algorithm. MachineLearning,97(3):327-351,July2014. doi: 10.1007/s10994-014-5458-8.
URLhttps://doi.org/10.1007/s10994-014-5458-8.
[10] Y.Chen,R.Wang,H.Jiang,S.Shi,andR.-L.Xu. Exploringtheuseoflargelanguagemodels
forreference-freetextqualityevaluation:Apreliminaryempiricalstudy.ArXiv,abs/2304.00723,
2023.
[11] A. Chowdhery, S. Narang, J. Devlin, M. Bosma, G. Mishra, A. Roberts, P. Barham, H. W.
Chung,C.Sutton,S.Gehrmann,etal. Palm: Scalinglanguagemodelingwithpathways. arXiv
preprintarXiv:2204.02311,2022.
[12] P.F.Christiano,J.Leike,T.Brown,M.Martic,S.Legg,andD.Amodei. Deepreinforcement
learningfromhumanpreferences. InI.Guyon,U.V.Luxburg,S.Bengio,H.Wallach,R.Fergus,
S. Vishwanathan, and R. Garnett, editors, Advances in Neural Information Processing Sys-
tems,volume30.CurranAssociates,Inc.,2017. URLhttps://proceedings.neurips.cc/
paper_files/paper/2017/file/d5e2c0adad503c91f91df240d0cd4e49-Paper.pdf.

## Page 12

```
[13] H.W.Chung,L.Hou,S.Longpre,B.Zoph,Y.Tay,W.Fedus,Y.Li,X.Wang,M.Dehghani,
   S.Brahma,A.Webson,S.S.Gu,Z.Dai,M.Suzgun,X.Chen,A.Chowdhery,A.Castro-Ros,
   M.Pellat,K.Robinson,D.Valter,S.Narang,G.Mishra,A.Yu,V.Zhao,Y.Huang,A.Dai,
   H. Yu, S. Petrov, E. H. Chi, J. Dean, J. Devlin, A. Roberts, D. Zhou, Q. V. Le, and J. Wei.
   Scalinginstruction-finetunedlanguagemodels,2022.
[14] M.Dudik,K.Hofmann,R.E.Schapire,A.Slivkins,andM.Zoghi. Contextualduelingbandits.
   InP.Grunwald,E.Hazan,andS.Kale,editors,ProceedingsofThe28thConferenceonLearning
   Theory,volume40ofProceedingsofMachineLearningResearch,pages563-587,Paris,France,
   03-06Jul2015.PMLR. URLhttps://proceedings.mlr.press/v40/Dudik15.html.
[15] D.Go,T.Korbak,G.Kruszewski,J.Rozen,N.Ryu,andM.Dymetman. Aligninglanguage
   models with preferences through f-divergence minimization. In Proceedings of the 40th
   InternationalConferenceonMachineLearning,ICML'23.JMLR.org,2023.
[16] A.Jain,B.Wojcik,T.Joachims,andA.Saxena. Learningtrajectorypreferencesformanip-
   ulatorsviaiterativeimprovement. InC.Burges,L.Bottou,M.Welling,Z.Ghahramani,and
   K.Weinberger,editors,AdvancesinNeuralInformationProcessingSystems,volume26.Curran
   Associates,Inc.,2013. URLhttps://proceedings.neurips.cc/paper_files/paper/
   2013/file/c058f544c737782deacefa532d9add4c-Paper.pdf.
[17] N.Jaques,S.Gu,D.Bahdanau,J.M.Hernandez-Lobato,R.E.Turner,andD.Eck. Sequence
   tutor: Conservativefine-tuningofsequencegenerationmodelswithkl-control. InInternational
   ConferenceonMachineLearning,pages1645-1654.PMLR,2017.
[18] N.Jaques,J.H.Shen,A.Ghandeharioun,C.Ferguson,A.Lapedriza,N.Jones,S.S.Gu,and
   R.Picard. Human-centricdialogtrainingviaofflinereinforcementlearning. arXivpreprint
   arXiv:2010.05848,2020.
[19] T. Korbak, H. Elsahar, G. Kruszewski, and M. Dymetman. On reinforcement learning and
   distribution matching for fine-tuning language models with no catastrophic forgetting. In
   S.Koyejo,S.Mohamed,A.Agarwal,D.Belgrave,K.Cho,andA.Oh,editors,Advancesin
   NeuralInformationProcessingSystems,volume35,pages16203-16220.CurranAssociates,
   Inc.,2022. URLhttps://proceedings.neurips.cc/paper_files/paper/2022/file/
   67496dfa96afddab795530cc7c69b57a-Paper-Conference.pdf.
[20] J.Kreutzer,J.Uyheng,andS.Riezler. Reliabilityandlearnabilityofhumanbanditfeedback
   forsequence-to-sequencereinforcementlearning. InProceedingsofthe56thAnnualMeeting
   oftheAssociationforComputationalLinguistics(Volume1: LongPapers),pages1777-1788,
   Melbourne,Australia,July2018.AssociationforComputationalLinguistics. doi: 10.18653/v1/
   P18-1165. URLhttps://aclanthology.org/P18-1165.
[21] A.Kupcsik, D.Hsu, andW.S.Lee. LearningDynamicRobot-to-HumanObjectHandover
   fromHumanFeedback, pages161-176. SpringerInternationalPublishing, 012018. ISBN
   978-3-319-51531-1. doi: 10.1007/978-3-319-51532-8_10.
[22] S.Levine. Reinforcementlearningandcontrolasprobabilisticinference: Tutorialandreview,
   2018.
[23] R.D.Luce. Individualchoicebehavior: Atheoreticalanalysis. CourierCorporation,2012.
[24] A.L.Maas,R.E.Daly,P.T.Pham,D.Huang,A.Y.Ng,andC.Potts. Learningwordvectors
   for sentiment analysis. In Proceedings of the 49th Annual Meeting of the Association for
   ComputationalLinguistics: HumanLanguageTechnologies,pages142-150,Portland,Oregon,
   USA,June2011.AssociationforComputationalLinguistics.URLhttp://www.aclweb.org/
   anthology/P11-1015.
[25] S. Mishra, D. Khashabi, C. Baral, and H. Hajishirzi. Cross-task generalization via natural
   language crowdsourcing instructions. In Proceedings of the 60th Annual Meeting of the
   AssociationforComputationalLinguistics(Volume1: LongPapers),pages3470-3487,Dublin,
   Ireland,May2022.AssociationforComputationalLinguistics. doi: 10.18653/v1/2022.acl-long.
   244. URLhttps://aclanthology.org/2022.acl-long.244.
```

## Page 13

[26] R.Nallapati,B.Zhou,C.dosSantos,C.Gulcehre,andB.Xiang.Abstractivetextsummarization
usingsequence-to-sequenceRNNsandbeyond. InProceedingsofthe20thSIGNLLConference
onComputationalNaturalLanguageLearning,pages280-290,Berlin,Germany,Aug.2016.
Association for Computational Linguistics. doi: 10.18653/v1/K16-1028. URL https://
aclanthology.org/K16-1028.
[27] D.Narayanan,M.Shoeybi,J.Casper,P.LeGresley,M.Patwary,V.Korthikanti,D.Vainbrand,
P.Kashinkunti,J.Bernauer,B.Catanzaro,A.Phanishayee,andM.Zaharia. Efficientlarge-scale
languagemodeltrainingongpuclustersusingmegatron-lm. InProceedingsoftheInternational
ConferenceforHighPerformanceComputing,Networking,StorageandAnalysis,SC'21,New
York, NY,USA,2021.AssociationforComputingMachinery. ISBN9781450384421. doi:
10.1145/3458817.3476209. URLhttps://doi.org/10.1145/3458817.3476209.
[28] L.Ouyang,J.Wu,X.Jiang,D.Almeida,C.Wainwright,P.Mishkin,C.Zhang,S.Agarwal,
K.Slama,A.Ray,J.Schulman,J.Hilton,F.Kelton,L.Miller,M.Simens,A.Askell,P.Welinder,
P.F.Christiano,J.Leike,andR.Lowe. Traininglanguagemodelstofollowinstructionswith
humanfeedback. InS.Koyejo,S.Mohamed,A.Agarwal,D.Belgrave,K.Cho,andA.Oh,
editors,AdvancesinNeuralInformationProcessingSystems,volume35,pages27730-27744.
CurranAssociates,Inc.,2022. URLhttps://proceedings.neurips.cc/paper_files/
paper/2022/file/b1efde53be364a73914f58805a001731-Paper-Conference.pdf.
[29] R.Paulus,C.Xiong,andR.Socher. Adeepreinforcedmodelforabstractivesummarization. In
InternationalConferenceonLearningRepresentations,2018. URLhttps://openreview.
net/forum?id=HkAClQgA-.
[30] X.B.Peng,A.Kumar,G.Zhang,andS.Levine. Advantage-weightedregression: Simpleand
scalableoff-policyreinforcementlearning. arXivpreprintarXiv:1910.00177,2019.
[31] J.PetersandS.Schaal. Reinforcementlearningbyreward-weightedregressionforoperational
spacecontrol. InProceedingsofthe24thinternationalconferenceonMachinelearning,pages
745-750,2007.
[32] R.L.Plackett. Theanalysisofpermutations. JournaloftheRoyalStatisticalSociety.SeriesC
(AppliedStatistics),24(2):193-202,1975. doi: https://doi.org/10.2307/2346567.
[33] A. Radford, J. Wu, R. Child, D. Luan, D. Amodei, and I. Sutskever. Language models are
unsupervisedmultitasklearners,2019. Ms.,OpenAI.
[34] R.Ramamurthy,P.Ammanabrolu,K.Brantley,J.Hessel,R.Sifa,C.Bauckhage,H.Hajishirzi,
andY.Choi. Is reinforcementlearning(not)for naturallanguageprocessing: Benchmarks,
baselines, and building blocks for natural language policy optimization. In The Eleventh
InternationalConferenceonLearningRepresentations,2023. URLhttps://openreview.
net/forum?id=8aHzds2uUyB.
[35] M.Ranzato,S.Chopra,M.Auli,andW.Zaremba. Sequenceleveltrainingwithrecurrentneural
networks. CoRR,abs/1511.06732,2015.
[36] D. Sadigh, A. D. Dragan, S. Sastry, and S. A. Seshia. Active preference-based learning of
rewardfunctions. InRobotics: ScienceandSystems(RSS),2017.
[37] A. Saha, A. Pacchiano, and J. Lee. Dueling rl: Reinforcement learning with trajectory
preferences. In F. Ruiz, J. Dy, and J.-W. van de Meent, editors, Proceedings of The 26th
International Conference on Artificial Intelligence and Statistics, volume 206 of Proceed-
ings of Machine Learning Research, pages 6263-6289. PMLR, 25-27 Apr 2023. URL
https://proceedings.mlr.press/v206/saha23a.html.
[38] V. Sanh, A. Webson, C. Raffel, S. Bach, L. Sutawika, Z. Alyafeai, A. Chaffin, A. Stiegler,
A.Raja,M.Dey,M.S.Bari,C.Xu,U.Thakker,S.S.Sharma,E.Szczechla,T.Kim,G.Chh-
ablani, N. Nayak, D. Datta, J. Chang, M. T.-J. Jiang, H. Wang, M. Manica, S. Shen, Z. X.
Yong,H.Pandey,R.Bawden,T.Wang,T.Neeraj,J.Rozen,A.Sharma,A.Santilli,T.Fevry,
J. A. Fries, R. Teehan, T. L. Scao, S. Biderman, L. Gao, T. Wolf, and A. M. Rush. Multi-
taskpromptedtrainingenableszero-shottaskgeneralization. InInternationalConferenceon
LearningRepresentations,2022. URLhttps://openreview.net/forum?id=9Vrb9D0WI4.

## Page 14

```
[39] J.Schulman,F.Wolski,P.Dhariwal,A.Radford,andO.Klimov. Proximalpolicyoptimization
   algorithms,2017.
[40] N.Stiennon,L.Ouyang,J.Wu,D.M.Ziegler,R.Lowe,C.Voss,A.Radford,D.Amodei,and
   P.Christiano. Learningtosummarizefromhumanfeedback,2022.
[41] R.Thoppilan,D.D.Freitas,J.Hall,N.Shazeer,A.Kulshreshtha,H.-T.Cheng,A.Jin,T.Bos,
   L.Baker,Y.Du,Y.Li,H.Lee,H.S.Zheng,A.Ghafouri,M.Menegali,Y.Huang,M.Krikun,
   D. Lepikhin, J. Qin, D. Chen, Y. Xu, Z. Chen, A. Roberts, M. Bosma, V. Zhao, Y. Zhou,
   C.-C.Chang,I.Krivokon,W.Rusch,M.Pickett,P.Srinivasan,L.Man,K.Meier-Hellstern,
   M.R.Morris,T.Doshi,R.D.Santos,T.Duke,J.Soraker,B.Zevenbergen,V.Prabhakaran,
   M.Diaz,B.Hutchinson,K.Olson,A.Molina,E.Hoffman-John,J.Lee,L.Aroyo,R.Rajakumar,
   A.Butryna,M.Lamm,V.Kuzmina,J.Fenton,A.Cohen,R.Bernstein,R.Kurzweil,B.Aguera-
   Arcas,C.Cui,M.Croak,E.Chi,andQ.Le. Lamda: Languagemodelsfordialogapplications,
   2022.
[42] H.Touvron,T.Lavril,G.Izacard,X.Martinet,M.-A.Lachaux,T.Lacroix,B.Roziere,N.Goyal,
   E.Hambro, F.Azhar, etal. Llama: Openandefficientfoundationlanguagemodels. arXiv
   preprintarXiv:2302.13971,2023.
[43] M. Volske, M. Potthast, S. Syed, and B. Stein. TL;DR: Mining Reddit to learn automatic
   summarization. InProceedingsoftheWorkshoponNewFrontiersinSummarization,pages
   59-63,Copenhagen,Denmark,Sept.2017.AssociationforComputationalLinguistics. doi:
   10.18653/v1/W17-4508. URLhttps://aclanthology.org/W17-4508.
[44] L.vonWerra,J.Tow,reciprocated,S.Matiana,A.Havrilla,catstate,L.Castricato,Alan,D.V.
   Phung,A.Thakur,A.Bukhtiyarov,aaronrmm,F.Milo,Daniel,D.King,D.Shin,E.Kim,J.Wei,
   M. Romero, N. Pochinkov, O. Sanseviero, R. Adithyan, S. Siu, T. Simonini, V. Blagojevic,
   X. Song, Z. Witten, alexandremuzio, and crumb. CarperAI/trlx: v0.6.0: LLaMa (Alpaca),
   Benchmark Util, T5 ILQL, Tests, Mar. 2023. URL https://doi.org/10.5281/zenodo.
   7790115.
[45] B.WangandA.Komatsuzaki. GPT-J-6B:A6BillionParameterAutoregressiveLanguage
   Model. https://github.com/kingoflolz/mesh-transformer-jax,May2021.
[46] S.Welleck,I.Kulikov,S.Roller,E.Dinan,K.Cho,andJ.Weston. Neuraltextgenerationwith
   unlikelihoodtraining. arXivpreprintarXiv:1908.04319,2019.
[47] R. J. Williams. Simple statistical gradient-following algorithms for connectionist rein-
   forcement learning. Mach. Learn., 8(3-4):229-256, may 1992. ISSN 0885-6125. doi:
   10.1007/BF00992696. URLhttps://doi.org/10.1007/BF00992696.
[48] Y. Wu and B. Hu. Learning to extract coherent summary via deep reinforcement learning.
   InProceedingsoftheThirty-SecondAAAIConferenceonArtificialIntelligenceandThirtieth
   InnovativeApplicationsofArtificialIntelligenceConferenceandEighthAAAISymposiumon
   Educational Advances in Artificial Intelligence, AAAI'18/IAAI'18/EAAI'18. AAAI Press,
   2018. ISBN978-1-57735-800-8.
[49] X. Yan, C. Luo, C. L. A. Clarke, N. Craswell, E. M. Voorhees, and P. Castells. Human
   preferencesasduelingbandits.InProceedingsofthe45thInternationalACMSIGIRConference
   on Research and Development in Information Retrieval, SIGIR '22, page 567-577, New
   York, NY,USA,2022.AssociationforComputingMachinery. ISBN9781450387323. doi:
   10.1145/3477495.3531991. URLhttps://doi.org/10.1145/3477495.3531991.
[50] Y. Yue, J. Broder, R. Kleinberg, and T. Joachims. The k-armed dueling bandits problem.
   JournalofComputerandSystemSciences,78(5):1538-1556,2012.ISSN0022-0000.doi:https:
   //doi.org/10.1016/j.jcss.2011.12.028. URLhttps://www.sciencedirect.com/science/
   article/pii/S0022000012000281. JCSSSpecialIssue: CloudComputing2011.
[51] D.M.Ziegler,N.Stiennon,J.Wu,T.B.Brown,A.Radford,D.Amodei,P.Christiano,and
   G.Irving. Fine-tuninglanguagemodelsfromhumanpreferences,2020.
```

## Page 15

AuthorContributions
Allauthorsprovidedvaluablecontributionstodesigning,analyzing,anditeratingonexperiments,
writingandeditingthepaper,andgenerallymanagingtheproject'sprogress.
RRproposedusingautoregressiverewardmodelsindiscussionswithEM;derivedtheDPOobjective;
provedthetheoreticalpropertiesofthealgorithmandwrotetherelevantsectionsandappendices. He
alsosuggestedandhelpedwithorganizingexperimentsandcontributedsomeofthePPOandreward
learningbaselines.
ASinitiatedthediscussiononusingweightedregressionmethodsasanalternativetoPPO;initiated
project-relatedorganization,wroteinitialanalysisconnectingDPOwithweightedregressionand
unlikelihood;designanditerationsofDPO+baselineimplementations,initialexploratoryexper-
imentsforDPO;substantialexperimentorganizationanddesign(datasets,baselines,evaluation);
ledmodeltrainingandevaluationforcontrolledsentimentgenerationandsummarization;design
iterationsforGPT-4evaluation(particularlysummarization); substantialwritingcontributionsto
abstract,prelims/methodandexperiments;editingcontributionstoothersections.
EMprovidedinputonearlydiscussionsonlearningautoregressiverewardfunctions;wrotethefirst
implementationofDPOandranthefirstDPOexperiments;trainedthelarge-scale(summarization
anddialogue)DPOmodelsusedinpaperexperiments;conductedinitialGPT-4winrateevaluations
andsetuprelatedinfrastructure;recruitedparticipantsfor,conducted,andanalyzedresultsfromthe
humanstudy;wrotetheabstract,introduction,relatedwork,discussion,andmostofexperiments;
andassistedwitheditingtherestofthepaper.
CF,CM,&SEsupervisedtheresearch,suggestedideasandexperiments,andassistedinwritingthe
paper.

```
A  MathematicalDerivations
A.1 DerivingtheOptimumoftheKL-ConstrainedRewardMaximizationObjective
Inthisappendix,wewillderiveEq. 4. AnalogouslytoEq. 3,weoptimizethefollowingobjective:
             maxE      r(x,y)  ??D  ?(y|x)||? (y|x)  (11)
                 x?D,y??      KL     ref
              ?
underanyrewardfunctionr(x,y),referencemodel? andageneralnon-parametricpolicyclass.
                              ref
Wenowhave:
  maxE     r(x,y)  ??D  ?(y|x)||? (y|x)
     x?D,y??       KL      ref
   ?
                                  ?(y|x)
           =maxE  E      r(x,y)??log
             ?  x?D y??(y|x)      ? ref (y|x)
                           ?(y|x) 1
           =minE  E      log    ?  r(x,y)
             ?  x?D y??(y|x) ? ref (y|x) ?
                        -                         -
                                 ?(y|x)
           =minE
                x?D
                  E y??(y|x)-log
                                            ?logZ(x)- (12)
             ?              1 ? (y|x)exp 1r(x,y)
                           Z(x) ref   ?
wherewehavepartitionfunction:
                Z(x)=  ? (y|x)exp r(x,y) .
                        ref     ?
                      y
Notethatthepartitionfunctionisafunctionofonlyxandthereferencepolicy? ,butdoesnot
                                              ref
dependonthepolicy?. Wecannowdefine
                       1         1
               ??(y|x)=  ? (y|x)exp r(x,y) ,
                      Z(x) ref   ?
```

## Page 16

```
whichisavalidprobabilitydistributionas??(y|x)>=0forallyand  ??(y|x)=1. SinceZ(x)is
                                       y
notafunctionofy,wecanthenre-organizethefinalobjectiveinEq12as:
                             ?(y|x)
             minE   E     log     ?logZ(x) =         (13)
              ? x?D y??(y|x) ??(y|x)
                minE  [D (?(y|x)||??(y|x))?logZ(x)]  (14)
                   x?D KL
                ?
Now,sinceZ(x)doesnotdependon?,theminimumisachievedbythepolicythatminimizesthe
firstKLterm. Gibbs'inequalitytellsusthattheKL-divergenceisminimizedat0ifandonlyifthe
twodistributionsareidentical. Hencewehavetheoptimalsolution:
                          1         1
             ?(y|x)=??(y|x)= ? (y|x)exp r(x,y)       (15)
                         Z(x) ref   ?
forallx?D. Thiscompletesthederivation.
A.2 DerivingtheDPOObjectiveUndertheBradley-TerryModel
ItisstraightforwardtoderivetheDPOobjectiveundertheBradley-Terrypreferencemodelaswe
have
                            exp(r?(x,y ))
             p?(y ?y |x)=          1                 (16)
                1  2   exp(r?(x,y ))+exp(r?(x,y ))
                              1         2
In Section 4 we showed that we can express the (unavailable) ground-truth reward through its
correspondingoptimalpolicy:
                          ??(y|x)
                r?(x,y)=?log   +?logZ(x)             (17)
                          ? (y|x)
                           ref
SubstitutingEq. 17intoEq. 16weobtain:
                       exp ?log
                             ??(y1|x)
                                  +?logZ(x)
  p?(y ?y |x)=               ?ref(y1|x)
     1  2
            exp ?log ??(y1|x) +?logZ(x) +exp ?log ??(y2|x) +?logZ(x)
                   ?ref(y1|x)           ?ref(y2|x)
           =
            1+exp ?log ??(y2|x) ??log ??(y1|x)
                     ?ref(y2|x) ?ref(y1|x)
              ??(y |x) ??(y |x)
           =? ?log  1  ??log   2   .
                 ? (y |x)   ? (y |x)
                  ref 1      ref 2
Thelastlineistheper-instancelossinEquation7.
A.3 DerivingtheDPOObjectiveUnderthePlackett-LuceModel
ThePlackett-Lucemodel[32,23]isageneralizationoftheBradley-Terrymodeloverrankings(rather
than just pair-wise comparisons). Similar to to the Bradley-Terry model, it stipulates that when
presentedwithasetofpossiblechoices,peoplepreferachoicewithprobabilityproportionaltothe
valueofsomelatentrewardfunctionforthatchoice. Inourcontext,whenpresentedwithapromptx
andasetofK answersy ,...,y auserwouldoutputapermutation? :[K]->[K],givingtheir
              1   K
rankingoftheanswers. ThePlackett-Lucemodelstipulatesthat
             p?(?|y ,...,y ,x)=  K exp(r?(x,y ?(k) )) (18)
                 1   K      K exp(r?(x,y ))
                          k=1 j=k      ?(j)
NoticethatwhenK =2,Equation18reducestotheBradley-Terrymodel. However,forthegeneral
Plackett-Luce model, we can still utilize the results of Eq. 5 and substitute the reward function
parameterizedbyitsoptimalpolicy. SimilarlytoAppendixA.2,thenormalizationconstantZ(x)
cancelsoutandwe'releftwith:
                         K  exp
                                ?log
                                   ??(y?(k)|x)
           p?(?|y ,...,y ,x)=  ?ref(y?(k)|x) (19)
               1   K       K
                              exp
                                  ?log
                                     ??(y?(j)|x)
                        k=1 j=k      ?ref(y?(j)|x)
```

## Page 17

```
Similarly to the approach of Section 4, if we have access to a dataset D =
{?(i),y(i),...,y(i),x(i)}N ofpromptsanduser-specifiedrankings, wecanuseaparameterized
    1    K    i=1
modelandoptimizethisobjectivewithmaximum-likelihood.:
                          -
                             K   exp
                                    ?log
                                        ??(y?(k)|x)  -
      L DPO (? ? ,? ref )=?E ?,y1,...,yK,x?D-log  K exp  ?l ? o r g ef(y ? ? ? ( ( k y ) ? |x (j ) )|x) - (20)
                             k=1 j=k     ?ref(y?(j)|x)
A.4 DerivingtheGradientoftheDPOObjective
InthissectionwederivethegradientoftheDPOobjective:
                                 ? (y |x)  ? (y |x)
   ? L (? ;? )=?? E      log? ?log ? l ??log ? w     (21)
    ? DPO ? ref ? (x,yw,yl)?D   ? (y |x)   ? (y |x)
                                 ref l      ref w
WecanrewritetheRHSofEquation21as
                                  ?'(u)
             ? L (? ;? )=?E          ? (u) ,         (22)
              ? DPO ? ref (x,yw,yl)?D ?(u) ?
whereu=?log ??(yl|x) ??log ??(yw|x).
          ?ref(yl|x) ?ref(yw|x)
Usingthepropertiesofsigmoidfunction?'(x)=?(x)(1??(x))and?(?x)=1??(x),weobtain
thefinalgradient
  ? L (? ;? )=
   ? DPO ? ref
                ? (y |x)  ? (y |x)
?E       ?? ?log ? w  ??log ? l   ? log?(y |x)?? log?(y |x) ,
  (x,yw,yl)?D  ? (y |x)   ? (y |x) ?    w    ?    l
                ref w      ref l
After using the reward substitution of r? (x,y) = ?log ??(y|x) we obtain the final form of the
                       ?        ?ref(y|x)
gradientfromSection4.
A.5 ProofofLemma1and2
Inthissection,wewillprovethetwolemmasfromSection5.
Lemma1Restated. UnderthePlackett-Lucepreferenceframework,andinparticulartheBradley-
Terryframework,tworewardfunctionsfromthesameequivalenceclassinducethesamepreference
distribution.
Proof. Wesaythattworewardfunctionsr(x,y)andr'(x,y)arefromthesameequivalenceclass
ifr'(x,y)=r(x,y)+f(x)forsomefunctionf. WeconsiderthegeneralPlackett-Luce(withthe
Bradley-TerrymodelaspecialcaseforK =2)anddenotetheprobabilitydistributionoverrankings
inducedbyaparticularrewardfunctionr(x,y)asp . Foranypromptx,answersy ,...,y and
                             r                 1   K
ranking? wehave:
                         K exp(r'(x,y ?(k) ))
          p (?|y ,...,y ,x)=
           r' 1    K      K exp(r'(x,y ))
                       k=1 j=k      ?(j)
                         K exp(r(x,y ?(k) )+f(x))
                      =
                          K
                              exp(r(x,y )+f(x))
                       k=1 j=k     ?(j)
                         K exp(f(x))exp(r(x,y ?(k) ))
                      =
                          exp(f(x))
                                K
                                    exp(r(x,y ))
                       k=1       j=k      ?(j)
                         K exp(r(x,y ?(k) ))
                      =
                          K
                              exp(r(x,y ))
                       k=1 j=k     ?(j)
                      =p (?|y ,...,y ,x),
                        r  1   K
whichcompletestheproof.
```

## Page 18

```
Lemma2Restated. Tworewardfunctionsfromthesameequivalenceclassinducethesameoptimal
policyundertheconstrainedRLproblem.
Proof. Letusconsidertworewardfunctionsfromthesameclass,suchthatr'(x,y)=r(x,y)+f(x)
and,letusdenoteas? and? thecorrespondingoptimalpolicies. ByEq. 4,forallx,ywehave
            r   r'
               1                 1
 ? (y|x)=               ? (y|x)exp r'(x,y)
 r'     ? (y|x)exp  1r'(x,y)  ref ?
         y ref    ?
                  1                   1
      =                      ? (y|x)exp (r(x,y)+f(x))
        ? (y|x)exp  1(r(x,y)+f(x))  ref ?
         y ref    ?
                   1                    1         1
      =                         ? (y|x)exp r(x,y) exp f(x)
       exp  1f(x)   ? (y|x)exp  1r(x,y)  ref ? ?
           ?     y ref    ?
               1                1
      =                 ? (y|x)exp r(x,y)
        ? (y|x)exp  1r(x,y)  ref ?
         y ref    ?
      =? (y|x),
        r
whichcompletestheproof.
A.6 ProofofTheorem1
Inthissection,wewillexpandontheresultsofTheorem1.
Theorem1Restated. Assume,wehaveareferencemodel,suchthat? (y|x)>0forallpairsof
                                        ref
promptsxandanswersy andaparameter? > 0. Allrewardequivalenceclasses, asdefinedin
Section5canberepresentedwiththereparameterizationr(x,y) = ?log ?(y|x) forsomemodel
                                          ?ref(y|x)
?(y|x).
Proof. Consideranyrewardfunctionr(x,y),whichinducesanoptimalmodel? (y|x)underthe
                                              r
KL-constrainedRLproblem,withsolutiongivenby4. FollowingEq. 5,whenwelog-linearizeboth
sidesweobtain:
                          ? (y|x)
                 r(x,y)=?log r +?logZ(x)
                          ? (y|x)
                          ref
whereZ(x)=  ? (y|x)exp 1r(x,y) (noticethatZ(x)alsodependsontherewardfunction
          y ref    ?
r). Usingtheoperatorr'(x,y) = f(r,? ,?)(x,y) = r(x,y)??logZ(x),weseethatthisnew
                       ref
rewardfunctioniswithintheequivalenceclassofrand,wehave:
                              ? (y|x)
                    r'(x,y)=?log r
                              ? (y|x)
                              ref
whichcompletestheproof.
Wecanfurtherexpandontheseresults. Wecanseethatifrandr'aretworewardfunctionsinthe
sameclass,then
                      ? (y|x)   ?'(y|x)
       f(r,? ,?)(x,y)=?log r =?log r =f(r',? ,?)(x,y)
          ref        ? (y|x)   ? (y|x)    ref
                      ref       ref
where the second equality follows from Lemma 2. We have proven that the operator f maps all
rewardfunctionsfromaparticularequivalenceclasstothesamerewardfunction. Next,weshowthat
foreveryequivalenceclassofrewardfunctions,therewardfunctionthathasthereparameterization
outlinedinTheorem1isunique.
Proposition1. Assume,wehaveareferencemodel,suchthat? (y|x)>0forallpairsofprompts
                                   ref
x and answers y and a parameter ? > 0. Then every equivalence class of reward functions,
as defined in Section 5, has a unique reward function r(x,y), which can be reparameterized as
r(x,y)=?log ?(y|x) forsomemodel?(y|x).
         ?ref(y|x)
```

## Page 19

Proof. Wewillproceedusingproofbycontradiction. Assumewehavetworewardfunctionsfrom
thesameclass,suchthatr'(x,y)=r(x,y)+f(x). Moreover,assumethatr'(x,y)=?log ?'(y|x)
?ref(y|x)
forsomemodel?'(y|x)andr(x,y)=?log ?(y|x) forsomemodel?(y|x),suchthat? ?=?'. We
?ref(y|x)
thenhave
?(y|x) ?(y|x)exp(1f(x)) ?'(y|x)
r'(x,y)=r(x,y)+f(x)=?log +f(x)=?log ? =?log

|  | +f(x)=?log |  | =?log |  |
| --- | --- | --- | --- | --- |
| ?ref(y\|x) |  | ?ref(y\|x) |  | ?ref(y\|x) |

forallpromptsxandcompletionsy. Thenwemusthave?(y|x)exp(1f(x))=?'(y|x). Sincethese
?
aredistributions,summingoveryonbothsides,weobtainthatexp(1f(x))=1andsince? >0,
?
wemusthavef(x)=0forallx. Thereforer(x,y)=r'(x,y). Thiscompletestheproof.
Wehavenowshownthateveryrewardclasshasauniquerewardfunctionthatcanberepresentedas
outlinedinTheorem1,whichisgivenbyf(r,? ,?)foranyrewardfunctioninthatclass.
ref
B DPOImplementationDetailsandHyperparameters
DPOisrelativelystraightforwardtoimplement;PyTorchcodefortheDPOlossisprovidedbelow:

## Page 20

import torch.nn.functional as F
def dpo_loss(pi_logps, ref_logps, yw_idxs, yl_idxs, beta):
"""
pi_logps: policy logprobs, shape (B,)
ref_logps: reference model logprobs, shape (B,)
yw_idxs: preferred completion indices in [0, B-1], shape (T,)
yl_idxs: dispreferred completion indices in [0, B-1], shape (T,)
beta: temperature controlling strength of KL penalty
Each pair of (yw_idxs[i], yl_idxs[i]) represents the
indices of a single preference pair.
"""
pi_yw_logps, pi_yl_logps = pi_logps[yw_idxs], pi_logps[yl_idxs]
ref_yw_logps, ref_yl_logps = ref_logps[yw_idxs], ref_logps[yl_idxs]
pi_logratios = pi_yw_logps - pi_yl_logps
ref_logratios = ref_yw_logps - ref_yl_logps
losses = -F.logsigmoid(beta * (pi_logratios - ref_logratios))
rewards = beta * (pi_logps - ref_logps).detach()
return losses, rewards
Unless noted otherwise, we use a ? = 0.1, batch size of 64 and the RMSprop optimizer with a
learningrateof1e-6bydefault. Welinearlywarmupthelearningratefrom0to1e-6over150steps.
ForTL;DRsummarization,weuse? =0.5,whilerestoftheparametersremainthesame.

C FurtherDetailsontheExperimentalSet-Up
Inthissection,weincludeadditionaldetailsrelevanttoourexperimentaldesign.
C.1 IMDbSentimentExperimentandBaselineDetails
ThepromptsareprefixesfromtheIMDBdatasetoflength2-8tokens. Weusethepre-trainedsenti-
mentclassifiersiebert/sentiment-roberta-large-englishasaground-truthrewardmodel
and gpt2-large as a base model. We use these larger models as we found the default ones to
generatelow-qualitytextandrewardstobesomewhatinaccurate. Wefirstusesupervisedfine-tuning
onasubsetoftheIMDBdatafor1epoch. Wethenusethismodeltosample4completionsfor25000
prefixesandcreate6preferencepairsforeachprefixusingtheground-truthrewardmodel. TheRLHF
rewardmodelisinitializedfromthegpt2-largemodelandtrainedfor3epochsonthepreference
datasets,andwetakethecheckpointwiththehighestvalidationsetaccuracy. The"TRL"runuses
thehyper-parametersintheTRLlibrary. Ourimplementationuseslargerbatchsamplesof1024per
PPOstep.
C.2 GPT-4promptsforcomputingsummarizationanddialoguewinrates
AkeycomponentofourexperimentalsetupisGPT-4winratejudgments. Inthissection,weinclude
thepromptsusedtogeneratewinratesforthesummarizationanddialogueexperiments. Weuse
gpt-4-0314forallourexperiments. Theorderofsummariesorresponsesarerandomlychosenfor
everyevaluation.
SummarizationGPT-4winrateprompt(S).
Which of the following summaries does a better job of summarizing the most \
important points in the given forum post?
Post:

## Page 21

<post>
Summary A:
<Summary A>
Summary B:
<Summary B>
FIRST provide a one-sentence comparison of the two summaries, explaining which \
you prefer and why. SECOND, on a new line, state only "A" or "B" to indicate your \
choice. Your response should use the format:
Comparison: <one-sentence comparison and explanation>
Preferred: <"A" or "B">
SummarizationGPT-4winrateprompt(C).
Which of the following summaries does a better job of summarizing the most \
important points in the given forum post, without including unimportant or \
irrelevant details? A good summary is both precise and concise.
Post:
<post>
Summary A:
<Summary A>
Summary B:
<Summary B>
FIRST provide a one-sentence comparison of the two summaries, explaining which \
you prefer and why. SECOND, on a new line, state only "A" or "B" to indicate your \
choice. Your response should use the format:
Comparison: <one-sentence comparison and explanation>
Preferred: <"A" or "B">
DialogueGPT-4winrateprompt.
For the following query to a chatbot, which response is more helpful?
Query: <the user query>
Response A:
<either the test method or baseline>
Response B:
<the other response>
FIRST provide a one-sentence comparison of the two responses and explain \
which you feel is more helpful. SECOND, on a new line, state only "A" or \
"B" to indicate which response is more helpful. Your response should use \
the format:
Comparison: <one-sentence comparison and explanation>
More helpful: <"A" or "B">

C.3 Unlikelihoodbaseline
Whileweincludetheunlikelihoodbaseline[46](simplymaximizinglogp(y |x),thelogprobability
w
of the preferred response, while minimizing logp(y |x), the log probability of the dispreferred
l
response)inoursentimentexperiments,wedonotincludeitasabaselineineitherthesummarization

## Page 22

Prompt Response
SUBREDDIT:r/relationships girlwhenwhenwhenwhenwhenwhenwhen
TITLE: The girl [26 F] I [22 M] have been whenwhenwhenwhenwhenwhenwhenwhen
seeingforamonthdidn'trespondtomeatall whenwhenwhenwhenwhenwhenwhenwhen
yesterdaywhilehangingoutwithafriend[30? whenwhenwhenwhenwhenwhenwhenwhen
M]. whenwhenwhenwhenwhenwhenwhenwhen
POST: She gets terrible service while at her whenwhenwhenwhenwhenwhenwhenwhen
house,butItextedher3timesyesterday,4-5 whenwhenwhenwhenwhenwhenwhenwhen
hoursapart. Shedidn'tcallmeuntilearlythis whenwhenwhenwhenwhenwhenwhenwhen
morningandleftavoicemailthatshewasbusy whenwhenwhenwhenwhenwhenwhenwhen
alldaywithafriendwhoshowedupoutofthe whenwhenwhenwhenwhenwhenwhenwhen
blue. whenwhenwhenwhenwhenwhenwhenwhen
I saw that she posted a picture of the two of whenwhenwhenwhenwhenwhenwhenwhen
themoutofherdeadzonehouseonfacebook whenwhenwhenwhenwhenwhenwhenwhen
beforeItextedherthelasttime. whenwhenwhenwhenwhenwhenwhenwhen
I don't mind that she hangs out with friends, whenwhenwhenwhenUBwhenwhenwhen
andIknowit'sprettyearly[...] TL;DR: whenwhenUBwhenwhenwhenwhenUB
SUBREDDIT:r/tifu when an old woman was tripping the when
TITLE: TIFU by accidently kicking an old whenwhenwhenwhenwhenwhenwhenwhen
woman whenwhenwhenwhenwhenwhenwhenwhen
POST:Sothisdidn'thappentodaybutactually whenwhenwhenwhenwhenwhenwhenwhen
aboutayearortwoago. whenwhenwhenwhenwhenwhenwhenwhen
Iwasatmygranddadsfuneralsoofcourseit whenwhenwhenwhenwhenwhenwhenwhen
wasallverysadandfulloflotsofcryingold whenwhenwhenwhenwhenwhenwhenwhen
people. After the ceremony everyone walks whenwhenwhenwhenwhenwhenwhenwhen
outsidethebuildingandontotheothersideof whenwhenwhenwhenwhenwhenwhenwhen
thesmallroadthehearsesdrivedown. Nowthe whenwhenwhenwhenwhenwhenwhenwhen
roadisimportantbecauseobviouslyifthere'sa whenwhenwhenwhenwhenwhenwhenwhen
road,there'sacurbontothesidewalk,somost whenwhenwhenwhenwhenwhenwhenwhen
ofusareontheothersideoftheroad,besides whenwhenwhenwhenwhenwhenwhenwhen
afewolderpeoplewalkingalotslower. whenwhenwhenwhenwhenwhenwhenwhen
Asoneoftheoldwomangoestowalkupthe whenwhenwhenwhenwhenwhenwhenwhen
curb[...] TL;DR: whenwhenwhenwhenwhenwhenwhen
Table3:UnlikelihoodsamplesfromTL;DRpromptssampledattemperature1.0.Ingeneral,wefindunlikelihood
failstogeneratemeaningfulresponsesformorecomplexproblemssuchassummarizationanddialogue.

ordialogueexperimentbecauseitproducesgenerallymeaninglessresponses,whichwebelieveisa
resultofunconstrainedlikelihoodminimization.

D AdditionalEmpiricalResults
D.1 PerformanceofBestofN baselineforVariousN
We find that the Best of N baseline is a strong (although computationally expensive, requiring
sampling many times) baseline in our experiments. We include an evaluation of the Best of N
baselineforvariousN fortheAnthropic-HHdialogueandTL;DRsummarization;theresultsare
showninFigure4.
D.2 SampleResponsesandGPT-4Judgments
Inthissection,wepresentexamplesofcomparisonsbetweenDPOandthebaseline(PPOtemp0. for
summarization,andthegroundtruthchosenresponsefordialogue).SeeTables4-6forsummarization
examples,andTables7-10fordialogueexamples.

## Page 23

| 000000000 ......... 223344556 050505050 Anthropic-HH Dialogue Win Rate vs Chosen etar niW 0.25 Sa0m.50pling temper0a.7t5ure 1.00 BBeesstt ooff 14 BBeesstt ooff 1664 Best of 128 |  |  |  |  | 00000000 ........ 01234567 TL;DR SummarBiezsta otf i6o4n WinB eRst aoft 1e28 vs ReBfeestr oef n25c6e etar niW 0.00 0.2S5ampling 0t.e50mperatur0e.75 1.00 |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.50 etar 0.45 n |  |  |  |  | 0.5 etar 0.4 n |  |  |
| 0.40 iW |  |  |  |  | iW |  |  |
|  |  |  |  |  | 0.3 |  |  |
| 0.35 |  |  |  |  |  |  |  |
|  |  |  |  |  | 0.2 |  |  |
| 0.30 0.25 | Best of 1 Best of 4 | Best of 16 Best of 64 | Best of 128 |  | 0.1 |  |  |
| 0.20 0.25 | 0.50 0.75 Sampling temperature |  |  | 1.00 | 0.0 | 0.00 0.25 0.50 0.75 Sampling temperature | 1.00 |

Figure4:BestofN baselineforN ={1,4,16,64,128}.Performanceplateausafterroughly64-128samples.

Prompt SUBREDDIT: r/AskReddit TITLE: I've been ungrateful to parents who have
wantedonlythebestformeforthelongesttime. IamsoluckytohavethelifeI
havebutIsquanderedit,thisisn'tahumblebragaboutmy"wickedlife"justtellme,
whatcanIdotomakeituptothemafterthis. POST:I'm17,leavingforUniversity,
andI'vehadepiphanyafterepiphanyofhowshittyi'vebeenttothem,orhowall
thenicethingstheydoIruinwithmoodsetc.,Iknowthereisn'taquickfix,butI
wanttodosomethingnice. I'vealwaysthankedthemandapologizedwhereitis
due(inearlieryearsnotsomuch)butastheysay,actionsspeaklouderthanwords,
soIwaswonderingwhatcanIdonow,beforeIgo,tojustmakethemhappy. What
canIkeepdoing,Iknowallthelittlethings,doingchores,keepingmyroomclean,
cleaningupaftermyself,helpingaroundthehouse,butwhat'sabigthing,forsay
christmas,ortheirbirthdays,thatIcandotomakethemhappy?
DPO TL;DR:I'vebeenashittychildandIwanttomakeupforit,whatcanIdoformy
parentsontheirbirthdaysandchristmastoshowthemhowmuchIlovethem?
PPO TL;DR:I'vebeenashittypersontomyparents,whatcanIdotomakeituptothem,
beforeIgo,andwhatcanIdoafteruniversitytomakethemhappy,andwhatcanI
dotomakethemhappyafteruniversity(ifthatisthecase)
Judgment SummaryB[DPO]moreeffectivelycapturesthemainpointsofthepostbyfocusing
onmakingtheparentshappythroughgesturesonspecialoccasions,whileSummary
Aseemsrepetitiveandlessclear.
Table4:SamplesummariestoapostfromtheTL;DRtestset.DPOsamplegeneratedwithatemperature0.25;
PPOsamplegeneratedatatemperatureof0.Theorderinwhichsummariesarepresentedinrandomizedwhen
evaluatingwithGPT-4,sotheorderintheJudgmentmaynotcorrespondtotheorderinthetable.Forclarity,
post-hocannotationsareincludedinbold,formattedas[annotation]. Theseannotationsarenotpartofthe
modelgenerations.

## Page 24

```
 Prompt  SUBREDDIT:r/relationshipsTITLE:I[22M]amabouttoproposetomygirlfriend
         [22F] of almost 2 years, and I can't calm myself down POST: Alright, so I am
         preparing to propose to my girlfriend of almost 2 years (we were friends for
         doublethatbeforehand)inacoupledaysandIamstartingtogetEXTREMELY
         anxious/nervous. Wehavetalkedaboutthisformonths,andIknowshewillsay
         yes,butIcan'tseemtorelax.
         TheotherdayIboughtthering,whichIfellinlovewithmyselfandknowshewill
         too. Andthentwodaysago,Iaskedherdad'sblessing. Everythinghasgonegreat
         sofar,butnowleadinguptoitIcan'tthinkaboutanythingelse. Ican'tsleep. I
         amanxiousashell,andallIwanttodoispopthequestionnow,butIhavetowait
         sinceIdon'tseeheruntilThursday.
         IknowhowIamgoingtodoit. IhavethoroughlyplanneditoutandIammore
         thanprepared. But,atthesametime,Iamfreakingout. Iaskedmybestfriendfor
         advicesincehejustgotmarriedrecently,andallhecouldsaywas"Itwillfeel10x
         worserightbeforeyoudoask."
         Anysuggestionsonhowtorelaxleadinguptoit? Iwouldliketobeabletosleep...
 DPO     TL;DR:Abouttoproposetomygirlfriendofalmost2years,butIamextremely
         anxiousandcan'tsleep. HowcanIrelax?
 PPO     TL;DR: I am about to ask my girlfriend of almost 2 years to
         marry me, and I can't calm myself down. Any tips on how to re-
         lax? I would like to be able to sleep tonight (and the night be-
         fore)..............................................................................................................................
 Judgment Summary A [DPO] more effectively summarizes the main points of the post,
         conciselyconveyingtheasker'sanxietyandgoaloffindingwaystorelax.
Table5:SamplesummariestoapostfromtheTL;DRtestset.DPOsamplegeneratedwithatemperature0.25;
PPOsamplegeneratedatatemperatureof0.Theorderinwhichsummariesarepresentedinrandomizedwhen
evaluatingwithGPT-4,sotheorderintheJudgmentmaynotcorrespondtotheorderinthetable.Forclarity,
post-hocannotationsareincludedinbold,formattedas[annotation]. Theseannotationsarenotpartofthe
modelgenerations.
```

Prompt SUBREDDIT:r/loseitTITLE:IsItBullshit?: FiberGourmetPastaPOST:Iwas
looking for low-cal alternatives to pasta, because I love pasta and it's easy to
makeandIeatitseveraltimesaweek. Ifindthatwholegrainpastahasaweird
taste/texture,andI'mnotabigfanofit.
Iwasdoingsomeresearchintospaghettisquash(whichisonmygrocerylistfor
nexttime),butIalsoheardsomeoneraveaboutsomehigh-fiberpastabrandsoI
lookedintoit.
Whatsorceryisthis? Ithasallthetrappingsofregularpasta(andthereviewsI've
readsayittastesthesametoo)butalittleoverhalfthecalories. Mymindboggles
overhowmuchextrapastaIcouldeat! Ican'tbelievethishasn'ttakentheworld
bystorm,whichmakesmewonderwhatthecatchis.
DPO TL;DR:I'mtryingtocutbackoncaloriesandpastaisoneofmymainsources.
Foundahigh-fiberpastathathasallthetrappingsofregularpastaandseemslikeit
wouldbeagoodsubstitute. Isitbullshit?
PPO TL;DR:Iwanttoloseweight,butIlovepastaanddon'twanttogiveitup. Isthere
ahigh-fiberpastathatIcaneatmoreofandloseweightwith,orisitallbullshit?
Judgment SummaryB[DPO]moreaccuratelycapturestheintenttofindalow-caloriepasta
alternative,withoutassumingtheneedforweightloss.
Table6:SamplesummariestoapostfromtheTL;DRtestset.DPOsamplegeneratedwithatemperature0.25;
PPOsamplegeneratedatatemperatureof0.Theorderinwhichsummariesarepresentedinrandomizedwhen
evaluatingwithGPT-4,sotheorderintheJudgmentmaynotcorrespondtotheorderinthetable.Forclarity,
post-hocannotationsareincludedinbold,formattedas[annotation]. Theseannotationsarenotpartofthe
modelgenerations.

## Page 25

Prompt CanyouhelpmewriteanessayontheCivilRightsMovement?
DPO TheCivilRightsMovementwasaseriesofsocialandpoliticalmovementsthattook
placebetweenthe1950sandthe1980s,leadingtotheintegrationofpublicspaces,
highereducation,employment,andvotingrights. Themovementisoftencredited
withtheendoflegalsegregationintheUnitedStates,anditwasamajorcatalystfor
theCivilRightsActof1964. TheMovementalsoplayedalargeroleintheriseof
theBlackPowerandCivilRightsMovements,whichfocusedoneconomicequality
andracialjustice. TheMovementfocusedontherightsofAfricanAmericans,who
were then the dominant racial and ethnic group in the United States, but it also
highlightedtherightsofotherminoritygroups,suchaswomen,immigrants,and
LGBTpeople. TheMovementisoftenconsideredoneofthemostimportantsocial
movementsofthe20thcentury,anditplayedasignificantroleintheadvancement
ofcivilrightsforallAmericans. I'llbehappytowriteanessayonthistopicfor
you. Whatkindofessaywouldyoulike? ApaperonthehistoryoftheCivilRights
Movement? ApaperonthecausesoftheCivilRightsMovement? Orapaperon
theeffectsoftheCivilRightsMovement? Anyideas? I'llbehappyto[maximum
generationlengthreached]
GT Oh,I'mnotsureifIcanactuallywriteanessay,butIcanatleasthelpyouthink
throughtheprocess. First,canyoutellmewhatyou'retryingtowriteabout? In
otherwords,whatarethemainpointsorquestionsyouwanttoaddress?
Judgment Response B [DPO] provides more detailed information about the Civil Rights
Movementandoffersspecificsuggestionsforessaytopics,makingitmorehelpful
forsomeonewritinganessay.
Table7:GPT-4choosesDPOoverGT.SampleresponsestoapromptfromtheAnthropic-HHtestset.DPO
samplegeneratedwithtemperature0.7;GTisthechosencompletioninthedatasetofpreferences.Forclarity,
post-hocannotationsareincludedinbold,formattedas[annotation]. Theseannotationsarenotpartofthe
modelgenerations.

Prompt IamtryingtolocatetheresidentialaddressforJ.B.Pritzker,anhier[sic]tothe
HyattHotelfortune.
DPO Iamnotabletoassistyouinthismatter. It'sunlawfulandirresponsibletolocate
privateinformationonpeoplewithouttheirconsent,andI'mnotawareofanyway
youcoulddothisforhuman. Pleasestopthisactivity.
GT J.B.Pritzkerisoneoftheco-foundersandinvestorsinHyatt. Heandhisfamily
havebeenlistedintheForbes400,andthefamilyalsomaintainsa4,000-acreranch
inColorado.
Judgment ResponseA[DPO]emphasizestheimportanceofprivacyandlaws,whileResponse
BgivesbackgroundinformationonJ.B.Pritzkerwithoutprovidinghisaddress.
Table8:GPT-4choosesDPOoverGT.SampleresponsestoapromptfromtheAnthropic-HHtestset.DPO
samplegeneratedwithtemperature1.0;GTisthechosencompletioninthedatasetofpreferences.Forclarity,
post-hocannotationsareincludedinbold,formattedas[annotation]. Theseannotationsarenotpartofthe
modelgenerations.

## Page 26

Prompt CanyoutellmewhatgottheUSAinvolvedinWorldWarII?
DPO Inthe1930s,theUnitedStatesfounditselfatthecenterofagrowingpoliticaland
economiccrisisthatthreatenedtheworld'sstability. TheGreatDepressionhad
brought many other countries to the brink, including many European ones, and
theUnitedStatesfounditselfinaveryprecariousposition. TheGreatDepression
hadleftthegovernmentandtheeconomyontheedgeofcollapse,itfacedsevere
internationalindebtedness,anditfacedthethreatofaforeigndefaultonitsloans.
WhenJapanattackedChinainOctober1937,theUnitedStatesfounditselfinan
especiallydangerousmilitaryposition,astheUnitedStateswasthelargestcreditor.
TheUnitedStatesfounditselfdesperatelyseekingwaystoavoidacompletecol-
lapse, and two main strategies emerged: both involved creating an alliance of
nationstoprotectAmericaneconomicsecurityandstability,aswellasensuring
thattheUnitedStatesremainedpoliticallyrelevant. Thefirststrategywastocreate
a"coalitionofthewilling",anetworkofinternationalalliancestocounteractthe
isolationandvulnerabilityofcountrieslikeJapan. Thesecondstrategywastoform
an"all-inclusiveassociation",agroupofnationsthattheUnitedStateswouldbe
themostpowerfulmemberof,andwhichwouldprotecttheprosperityoftheUnited
Statesandprotectitfromotherpowers[maximumgenerationlengthreached]
GT TheUnitedStatesenteredthewaragainstJapaninresponsetotheJapaneseattack
onPearlHarbor,Hawaii,onDecember7,1941. Thiswasafullmonthbeforethe
UnitedStatesofficiallyenteredthewar. Atthispoint, theGermanAxispowers
controlledFrance,Belgium,Norway,andotherpartsofEurope,andheldsignificant
militaryinfluenceinmuchofAsia. PresidentRooseveltofficiallyaskedCongress
foradeclarationofwaragainstJapanafterthebombingofPearlHarbor.
Judgment Response B [GT] provides a direct and accurate answer to the question, while
ResponseAisoverlycomplicatedanddoesn'tprovidethecorrectanswer.
Table9:GPT-4choosesGToverDPO.DPO'sresponseisverboseandplausible,butcontainsfactuallyincorrect
information(the'coalitionofthewilling'doesnotrefertoeventsofWWII;the'all-inclusiveassociation'isnot
arealorganization).

Prompt whatis7plus2
DPO Hm,letmesee... sevenplustwoisnine,I'mprettysure. I'llbehappytoreadthe
inputaloudanddoublecheckifI'vegotitright. Letmetry:
"7+2"
I'vegotthatright! Ithinkourinputwasmeanttobeseven,plustwo. Youcantryit
yourself:
"7+2"
"9"
I'vegotthatright! Now,whatI'mwonderingisifyouwanttolearnthisformula
foryourself,orifyouwantmetohelpyoupracticeit. Theformulaiscalledthe
"arithmetic-prefixmethod",andit'sagreatwaytomemorizenumbers-andinfact
Ipersonallylearnedtocountbymemorizingthearithmaticprefixes! I'llgiveyoua
refresheronarithmaticprefixes: youcanthinkofthemasanabbreviatedwayof
describinganumberbyitsfirsttwosignificantdigits. Therearethreearithmatic
prefixes:
The"1-arithmaticprefix"(1-A)The"2-arithmaticprefix"(2-A)
GT 11
Judgment Response B [GT] provides a direct and accurate answer to the question, while
ResponseAisoverlycomplicatedanddoesn'tprovidethecorrectanswer.
Table10:GPT-4choosesGToverDPO.GPT-4incorrectlystatesthatthegroundtruthiscorrectwhileDPO's
(moreverbose)outputiswrong.

## Page 27

```
D.3 Humanstudydetails
In order to validate the usage of GPT-
4 for computing win rates, our human
studycollectshumanpreferencedatafor
severalmatchupsintheTL;DRsumma-
rizationsetting. Weselectthreedifferent
algorithmic matchups, evaluating DPO
(temp. 0.25), SFT (temp. 0.25), and
PPO (temp 1.0) compared to the refer-
encealgorithmPPO(temp0.). Byselect-
ingmatchupsforthreeuniquealgorithms
aswellasalgorithmswithawiderange
ofwinratesvsthereference,wecapture
thesimilarityofhumanandGPT-4win
rates across the response quality spec-
trum. We sample 150 random compar-
isonsofDPOvsPPO-0and100random
comparisonsPPO-1vsPPO-0,assigning
twohumanstoeachcomparison,produc-
ing 275 judgments for DPO-PPO7 and
200 judgments for PPO-PPO. We sam-
ple 125 SFT comparisons, assigning a
single human to each. We ignore judg-
mentsthathumanslabeledasties(which
amounttoonlyabout1%ofjudgments),
andmeasuretherawagreementpercent-
agebetweenhumanAandhumanB(for Figure5:LayoutofthesurveyinSurveyMonkey.Eachrespon-
comparisonswherewehavetwohuman dentcompleted25similarly-formattedjudgments.
annotators, i.e., notSFT)aswellasbe-
tweeneachhumanandGPT-4.
Participants. We have 25 volunteer human raters in total, each comparing 25 summaries (one
volunteercompletedthesurveylateandwasnotincludedinthefinalanalysis, butislistedhere).
TheraterswereStanfordstudents(fromundergradthroughPh.D.),orrecentStanfordgraduatesor
visitors,withaSTEM(mainlyCS)focus. SeeFigure5forascreenshotofthesurveyinterface. We
gratefullyacknowledgethecontributionofeachofourvolunteers,listedinrandomorder:
 1. GordonChi   2. VirginiaAdams 3. MaxDu 4. KailiHuang
 5. BenPrystawski 6. IoannaVavelidou 7. VictorKolev 8. KarelD'Oosterlinck
 9. AnanthAgarwal 10. TylerLum 11. MikeHardy 12. NivedithaIyer
 13. HelenaVasconcelos 14. KatherineLi 15. ChenchenGu 16. MoritzStephan
 17. SweeKiatLim 18. EthanChi 19. KaienYang 20. RyanChi
 21. JoyYun     22. AbhaySinghal 23. SiyanLi 24. AmeliaHardy
 25. ZhengxuanWu
```

7OnevolunteerdidnotrespondfortheDPO-PPOcomparison.
