package com.example.speako.domain.presentation.service;

import com.example.speako.domain.highlight.entity.HighlightCategory;
import com.example.speako.domain.highlight.entity.PronunciationHighlight;
import com.example.speako.domain.highlight.repository.PronunciationHighlightRepository;
import com.example.speako.domain.presentation.dto.PresentationRequestDTO;
import com.example.speako.domain.presentation.dto.PresentationResponseDTO;
import com.example.speako.domain.presentation.entity.Presentation;
import com.example.speako.domain.presentation.entity.Slide;
import com.example.speako.domain.presentation.repository.PresentationRepository;

import com.example.speako.domain.script.entity.Script;
import com.example.speako.domain.script.repository.ScriptRepository;
import com.example.speako.domain.slide.repository.SlideRepository;
import com.example.speako.domain.user.entity.User;
import com.example.speako.domain.user.repository.UserRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class PresentationService {
    private final PronunciationHighlightRepository pronunciationHighlightRepository;
    private final UserRepository userRepository;
    private final PresentationRepository presentationRepository;
    private final ScriptRepository scriptRepository;
    private final SlideRepository slideRepository;
    private final S3Service s3Service;
    private final ObjectMapper objectMapper = new ObjectMapper();

    private static final String AI_BASE_URL =
            System.getenv().getOrDefault("AI_BASE_URL",
                    "https://losing-charity-floyd-transparent.trycloudflare.com");

    private static void addApiKeyHeader(HttpHeaders headers) {
        String apiKey = System.getenv("SPEAKO_API_KEY");
        if (apiKey != null && !apiKey.isBlank()) {
            headers.set("X-API-Key", apiKey);
        }
    }

    @Transactional
    public Long generatePresentationAndScript(MultipartFile file, PresentationRequestDTO.CreateDTO request, String email) {
        User user = userRepository.findByEmail(email)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 회원입니다."));

        if (file.isEmpty() || file.getOriginalFilename() == null) {
            throw new IllegalArgumentException("업로드된 파일이 비어있습니다.");
        }

        String originalFilename = file.getOriginalFilename();
        String extension = originalFilename.substring(originalFilename.lastIndexOf(".") + 1).toLowerCase();

        if (!extension.equals("pptx") && !extension.equals("pdf")) {
            throw new IllegalArgumentException("PPTX 또는 PDF 파일만 업로드 가능합니다.");
        }

        long maxFileSize = 20 * 1024 * 1024; // 20MB
        if (file.getSize() > maxFileSize) {
            throw new IllegalArgumentException("파일 용량은 최대 20MB를 초과할 수 없습니다.");
        }

        float fileSizeMB = (float) file.getSize() / (1024 * 1024);

        final byte[] fileBytes;
        try {
            fileBytes = file.getBytes();
        } catch (IOException e) {
            throw new RuntimeException("파일 읽기 실패", e);
        }

        String fileUrl = s3Service.uploadFile(file);

        long aiProjectId;
        int slideCount;
        {
            Map aiResponse = createAiProject(fileBytes, originalFilename, request.getTopic());
            aiProjectId = ((Number) aiResponse.get("project_id")).longValue();
            slideCount = extractSlideCount(aiResponse);
        }

        Presentation presentation = Presentation.builder()
                .user(user)
                .fileName(originalFilename)
                .fileType(extension.equals("pdf") ? Presentation.FileType.pdf : Presentation.FileType.ppt)
                .fileSize(fileSizeMB)
                .slideCount(slideCount)
                .aiProjectId(aiProjectId)
                .topic(request.getTopic())
                .duration(request.getDuration())
                .tone(Presentation.Tone.valueOf(request.getTone()))
                .guideline(request.getGuideline())
                .fileUrl(fileUrl)
                .build();

        presentationRepository.save(presentation);

        callPythonAiServerForScript(presentation, request.getTopic(), request.getDuration(), request.getTone(), request.getGuideline());

        return presentation.getPresentationId();
    }

    private Map createAiProject(byte[] fileBytes, String filename, String topic) {
        RestTemplate restTemplate = new RestTemplate();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10_000);
        factory.setReadTimeout(300_000);
        restTemplate.setRequestFactory(factory);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);
        addApiKeyHeader(headers);

        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("file", new ByteArrayResource(fileBytes) {
            @Override
            public String getFilename() {
                return filename;
            }
        });
        if (topic != null && !topic.isBlank()) {
            body.add("topic", topic);
        }

        try {
            ResponseEntity<Map> response = restTemplate.postForEntity(
                    AI_BASE_URL + "/api/projects",
                    new HttpEntity<>(body, headers),
                    Map.class
            );
            Map responseBody = response.getBody();
            if (responseBody == null || !(responseBody.get("project_id") instanceof Number)) {
                throw new IllegalStateException("AI 서버 업로드 응답에 project_id가 없습니다: " + responseBody);
            }
            return responseBody;
        } catch (HttpStatusCodeException e) {
            throw new ResponseStatusException(e.getStatusCode(), extractDetail(e.getResponseBodyAsString()));
        }
    }

    @SuppressWarnings("unchecked")
    private int extractSlideCount(Map aiResponse) {
        Object data = aiResponse.get("data");
        if (data instanceof Map) {
            Object slides = ((Map<String, Object>) data).get("slides");
            if (slides instanceof List) {
                return ((List<?>) slides).size();
            }
        }
        return 0;
    }

    private void callPythonAiServerForScript(Presentation presentation, String topic, Integer duration, String tone, String guideline) {
        RestTemplate restTemplate = new RestTemplate();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10_000);
        factory.setReadTimeout(30_000);
        restTemplate.setRequestFactory(factory);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        addApiKeyHeader(headers);

        Map<String, Object> body = new HashMap<>();
        body.put("project_id", presentation.getAiProjectId());
        body.put("topic", topic);
        body.put("presentation_time", duration);
        body.put("style", tone);
        body.put("extra_requirement", guideline);

        HttpEntity<Map<String, Object>> requestEntity = new HttpEntity<>(body, headers);

        String jobId;
        try {
            ResponseEntity<Map> response = restTemplate.postForEntity(
                    AI_BASE_URL + "/api/script/full",
                    requestEntity,
                    Map.class
            );

            Map responseBody = response.getBody();
            if (responseBody == null || !responseBody.containsKey("job_id")) {
                throw new IllegalStateException("AI 서버로부터 Job ID를 받지 못했습니다.");
            }
            jobId = responseBody.get("job_id").toString();

        } catch (HttpStatusCodeException e) {
            String errorBody = e.getResponseBodyAsString();
            System.err.println("=== AI 서버 에러 상세 내용 ===");
            System.err.println(errorBody);
            throw new ResponseStatusException(e.getStatusCode(), "AI 대본 생성 요청 실패: " + errorBody);
        }

        HttpEntity<Void> pollEntity = new HttpEntity<>(headers);
        int maxRetry = 120;
        boolean isCompleted = false;

        for (int i = 0; i < maxRetry; i++) {
            try {
                Thread.sleep(3000);

                ResponseEntity<Map> statusResponse = restTemplate.exchange(
                        AI_BASE_URL + "/api/script/jobs/" + jobId,
                        HttpMethod.GET,
                        pollEntity,
                        Map.class
                );

                Map statusBody = statusResponse.getBody();
                if (statusBody != null) {
                    String status = (String) statusBody.get("status");

                    if ("completed".equalsIgnoreCase(status)) {
                        saveScripts(presentation, statusBody);
                        isCompleted = true;
                        break;
                    } else if ("failed".equalsIgnoreCase(status)) {
                        throw new RuntimeException("AI 대본 생성 작업이 실패했습니다: " + statusBody.get("error"));
                    }
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new RuntimeException("폴링 대기 중 인터럽트가 발생했습니다.", e);
            } catch (HttpStatusCodeException e) {
                System.err.println("폴링 중 오류(계속 시도): " + e.getStatusCode());
            }
        }

        if (!isCompleted) {
            throw new IllegalStateException("대본 생성이 제한 시간 안에 끝나지 않았습니다 (job_id=" + jobId + ")");
        }
    }

    @SuppressWarnings("unchecked")
    private void saveScripts(Presentation presentation, Map statusBody) {
        Map<String, Object> data = (Map<String, Object>) statusBody.get("data");
        if (data == null) {
            throw new IllegalStateException("완료 응답에 data가 없습니다: " + statusBody);
        }
        List<Map<String, Object>> slides = (List<Map<String, Object>>) data.get("slides");
        if (slides == null || slides.isEmpty()) {
            throw new IllegalStateException("완료 응답에 slides가 없습니다: " + data);
        }

        for (Map<String, Object> item : slides) {
            int slideNumber = Integer.parseInt(String.valueOf(item.get("slide_number")));
            String scriptText = String.valueOf(item.get("script"));

            Slide slide = Slide.builder()
                    .presentation(presentation)
                    .slideOrder(slideNumber)
                    .build();
            slideRepository.save(slide);

            Script script = Script.builder()
                    .slide(slide)
                    .content(scriptText)
                    .build();
            scriptRepository.save(script);
        }
    }

    private String extractDetail(String responseBody) {
        try {
            JsonNode node = objectMapper.readTree(responseBody);
            if (node.has("detail")) {
                return node.get("detail").asText();
            }
        } catch (Exception ignored) {
        }
        return responseBody;
    }

    @Transactional(readOnly = true)
    public PresentationResponseDTO.DetailDTO getPresentationDetails(Long presentationId) {
        Presentation presentation = presentationRepository.findById(presentationId)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 발표 자료입니다."));

        List<PresentationResponseDTO.SlideScriptDTO> slideScripts = presentation.getSlides().stream()
                .map(slide -> {
                    Script latestScript = slide.getScripts().stream()
                            .max(Comparator.comparing(Script::getVersion))
                            .orElse(null);

                    return PresentationResponseDTO.SlideScriptDTO.builder()
                            .slideId(slide.getSlideId())
                            .slideOrder(slide.getSlideOrder())
                            .slideTitle(slide.getSlideTitle())
                            .rawText(slide.getRawText())
                            .scriptId(latestScript != null ? latestScript.getScriptId() : null)
                            .content(latestScript != null ? latestScript.getContent() : "생성된 대본이 없습니다.")
                            .version(latestScript != null ? latestScript.getVersion() : 1)
                            .build();
                })
                .sorted(Comparator.comparing(PresentationResponseDTO.SlideScriptDTO::getSlideOrder))
                .collect(Collectors.toList());

        return PresentationResponseDTO.DetailDTO.builder()
                .presentationId(presentation.getPresentationId())
                .topic(presentation.getTopic())
                .duration(presentation.getDuration())
                .tone(presentation.getTone().name())
                .fileUrl(presentation.getFileUrl())
                .slides(slideScripts)
                .build();
    }

    /** 전체 대본 재생성 (유저 수정 대본 및 설정값 반영) */
    @Transactional
    public PresentationResponseDTO.DetailDTO regenerateAll(Long presentationId, Integer duration, String tone, String extraRequirement, String currentScript) {
        Presentation presentation = presentationRepository.findById(presentationId)
                .orElseThrow(() -> new IllegalArgumentException("발표를 찾을 수 없습니다."));
        if (presentation.getAiProjectId() == null) {
            throw new IllegalStateException("이 발표에는 AI 프로젝트 번호가 없습니다. 자료를 다시 업로드해주세요.");
        }

        // 기존 슬라이드·대본 삭제
        List<Slide> old = slideRepository.findByPresentation(presentation);
        for (Slide s : old) {
            scriptRepository.deleteBySlide(s);
        }
        slideRepository.deleteAll(old);
        slideRepository.flush();

        if (duration != null) presentation.updateDuration(duration);
        if (tone != null && !tone.isBlank()) presentation.updateTone(Presentation.Tone.valueOf(tone));
        if (extraRequirement != null) presentation.updateGuideline(extraRequirement);

        callPythonAiServerForScript(presentation, presentation.getTopic(), presentation.getDuration(), presentation.getTone().name(), presentation.getGuideline());
        scriptRepository.flush();
        return getPresentationDetails(presentationId);
    }

    /** 슬라이드 하나만 부분 재생성 (유저가 수정한 기존 대본 반영) */
    @Transactional
    public PresentationResponseDTO.DetailDTO regenerateOne(Long presentationId, Long scriptId, String tone, String extraRequirement, String currentScript) {
        Script script = scriptRepository.findWithSlideAndPresentation(scriptId)
                .orElseThrow(() -> new IllegalArgumentException("대본을 찾을 수 없습니다."));

        Presentation presentation = script.getSlide().getPresentation();
        if (!presentation.getPresentationId().equals(presentationId)) {
            throw new IllegalArgumentException("이 발표의 대본이 아닙니다.");
        }
        if (presentation.getAiProjectId() == null) {
            throw new IllegalStateException("이 발표에는 AI 프로젝트 번호가 없습니다. 자료를 다시 업로드해주세요.");
        }

        RestTemplate restTemplate = new RestTemplate();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10_000);
        factory.setReadTimeout(120_000);
        restTemplate.setRequestFactory(factory);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        addApiKeyHeader(headers);

        Map<String, Object> body = new HashMap<>();
        body.put("project_id", presentation.getAiProjectId());
        body.put("target_slide", script.getSlide().getSlideOrder());
        body.put("style", (tone != null && !tone.isBlank()) ? tone : presentation.getTone().name());
        body.put("extra_requirement", (extraRequirement != null && !extraRequirement.isBlank()) ? extraRequirement : presentation.getGuideline());

        if (currentScript != null && !currentScript.isBlank()) {
            body.put("current_script", currentScript);
        } else {
            body.put("current_script", script.getContent());
        }

        Map response;
        try {
            response = restTemplate.postForEntity(
                    AI_BASE_URL + "/api/script/partial", new HttpEntity<>(body, headers), Map.class).getBody();
        } catch (HttpStatusCodeException e) {
            throw new ResponseStatusException(e.getStatusCode(), extractDetail(e.getResponseBodyAsString()));
        }

        Map<String, Object> data = response == null ? null : (Map<String, Object>) response.get("data");
        if (data == null || data.get("script") == null) {
            throw new IllegalStateException("AI 서버가 재생성 대본을 주지 않았습니다: " + response);
        }

        script.updateContent(String.valueOf(data.get("script")));
        scriptRepository.save(script);

        return getPresentationDetails(presentationId);
    }

    //전체 대본 생성
    @Transactional(readOnly = true)
    public PresentationResponseDTO.FullScriptViewDTO getFullScriptForRecording(Long presentationId) {
        Presentation presentation = presentationRepository.findById(presentationId)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 발표 자료입니다."));

        List<PresentationResponseDTO.SlideScriptDTO> slideScripts = presentation.getSlides().stream()
                .map(slide -> {
                    Script latestScript = slide.getScripts().stream()
                            .max(Comparator.comparing(Script::getVersion))
                            .orElse(null);

                    return PresentationResponseDTO.SlideScriptDTO.builder()
                            .slideId(slide.getSlideId())
                            .slideOrder(slide.getSlideOrder())
                            .slideTitle(slide.getSlideTitle())
                            .rawText(slide.getRawText())
                            .scriptId(latestScript != null ? latestScript.getScriptId() : null)
                            .content(latestScript != null ? latestScript.getContent() : "")
                            .version(latestScript != null ? latestScript.getVersion() : 1)
                            .build();
                })
                .sorted(Comparator.comparing(PresentationResponseDTO.SlideScriptDTO::getSlideOrder))
                .collect(Collectors.toList());

        String combinedFullScript = slideScripts.stream()
                .map(PresentationResponseDTO.SlideScriptDTO::getContent)
                .filter(content -> content != null && !content.isBlank())
                .collect(Collectors.joining("\n\n"));

        return PresentationResponseDTO.FullScriptViewDTO.builder()
                .presentationId(presentation.getPresentationId())
                .topic(presentation.getTopic())
                .duration(presentation.getDuration())
                .fileUrl(presentation.getFileUrl())
                .combinedScript(combinedFullScript)
                .slideScripts(slideScripts)
                .build();
    }

    /**
     * 커스텀 대본 등록 (파일 업로드 또는 수기 텍스트 입력)
     */
    @Transactional
    public Long createPresentationForCustomScript(String email, MultipartFile scriptFile, String scriptText, String topic) {
        User user = userRepository.findByEmail(email)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 회원입니다."));

        if ((scriptFile == null || scriptFile.isEmpty()) && (scriptText == null || scriptText.isBlank())) {
            throw new IllegalArgumentException("대본 파일이나 수기 텍스트 중 하나는 반드시 입력해야 합니다.");
        }

        String fileName = "수기 작성 대본";
        Presentation.FileType fileType = Presentation.FileType.pdf;
        float fileSizeMB = 0.0f;
        String fileUrl = "";

        if (scriptFile != null && !scriptFile.isEmpty()) {
            fileName = scriptFile.getOriginalFilename();
            if (fileName == null || !fileName.contains(".")) {
                throw new IllegalArgumentException("유효하지 않은 파일명입니다.");
            }

            String extension = fileName.substring(fileName.lastIndexOf(".") + 1).toLowerCase();
            if (!extension.equals("docx") && !extension.equals("txt") && !extension.equals("pdf")) {
                throw new IllegalArgumentException("지원하지 않는 파일 형식입니다. (docx, txt, pdf만 가능)");
            }

            long maxFileSize = 20 * 1024 * 1024;
            if (scriptFile.getSize() > maxFileSize) {
                throw new IllegalArgumentException("대본 파일 용량은 최대 20MB를 초과할 수 없습니다.");
            }

            fileSizeMB = (float) scriptFile.getSize() / (1024 * 1024);
            fileType = extension.equals("pdf") ? Presentation.FileType.pdf : Presentation.FileType.ppt;

            fileUrl = s3Service.uploadFile(scriptFile);
        } else {
            fileUrl = "https://s3.amazonaws.com/speako-presentations/custom-text-" + System.currentTimeMillis() + ".txt";
        }

        Long aiProjectId = generateAiProjectIdForCustom(scriptText, scriptFile);

        Presentation presentation = Presentation.builder()
                .user(user)
                .fileName(fileName)
                .fileType(fileType)
                .fileSize(fileSizeMB)
                .slideCount(1)
                .aiProjectId(aiProjectId)
                .topic(topic != null && !topic.isBlank() ? topic : "커스텀 대본 발표")
                .duration(60)
                .tone(Presentation.Tone.formal)
                .guideline("")
                .fileUrl(fileUrl)
                .build();

        presentationRepository.save(presentation);

        String scriptContent = "";
        if (scriptText != null && !scriptText.isBlank()) {
            scriptContent = scriptText;
        } else if (scriptFile != null && !scriptFile.isEmpty()) {
            try {
                scriptContent = new String(scriptFile.getBytes(), java.nio.charset.StandardCharsets.UTF_8);
            } catch (IOException e) {
                scriptContent = "파일 내용을 읽어올 수 없습니다.";
            }
        }

        Slide slide = Slide.builder()
                .presentation(presentation)
                .slideOrder(1)
                .slideTitle("커스텀 대본")
                .rawText(scriptContent)
                .build();
        slideRepository.save(slide);

        Script script = Script.builder()
                .slide(slide)
                .content(scriptContent)
                .version(1)
                .build();
        scriptRepository.save(script);

        requestAndSaveHighlights(aiProjectId, script);

        return presentation.getPresentationId();
    }

    // ai한테 하이라이팅 받아 오는 메서드
    @SuppressWarnings("unchecked")
    private void requestAndSaveHighlights(Long aiProjectId, Script script) {
        RestTemplate restTemplate = new RestTemplate();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10_000);
        factory.setReadTimeout(30_000);
        restTemplate.setRequestFactory(factory);

        HttpHeaders headers = new HttpHeaders();
        addApiKeyHeader(headers);
        HttpEntity<Void> requestEntity = new HttpEntity<>(headers);

        try {
            ResponseEntity<Map> response = restTemplate.exchange(
                    AI_BASE_URL + "/api/project/" + aiProjectId + "/highlights",
                    HttpMethod.GET,
                    requestEntity,
                    Map.class
            );

            Map responseBody = response.getBody();
            if (responseBody != null && responseBody.containsKey("highlights")) {
                List<Map<String, Object>> highlightList = (List<Map<String, Object>>) responseBody.get("highlights");

                for (Map<String, Object> h : highlightList) {
                    String categoryStr = (String) h.get("category");
                    HighlightCategory category = HighlightCategory.MISMATCH; // 기본값으로 채울 안전한 Enum 선택

                    try {
                        if (categoryStr != null && !categoryStr.isBlank()) {
                            category = HighlightCategory.valueOf(categoryStr.toUpperCase());
                        }
                    } catch (IllegalArgumentException e) {
                        // AI가 정의되지 않은 카테고리를 보냈을 때 로그를 남기고 기본값(MISMATCH 등)으로 처리
                        System.err.println("알 수 없는 카테고리 값 수신: " + categoryStr + ", 기본값으로 대체합니다.");
                    }

                    PronunciationHighlight highlight = PronunciationHighlight.builder()
                            .script(script)
                            .word((String) h.get("word"))
                            .standardPronunciation((String) h.get("standard_pronunciation"))
                            .category(category)
                            .ruleDesc((String) h.get("rule_desc"))
                            .positionStart(Integer.parseInt(String.valueOf(h.get("position_start"))))
                            .positionEnd(Integer.parseInt(String.valueOf(h.get("position_end"))))
                            .build();

                    pronunciationHighlightRepository.save(highlight);
                }
            }
        } catch (Exception e) {
            System.err.println("하이라이팅 결과 수신 실패: " + e.getMessage());
        }
    }

    /**
     * AI 서버의 커스텀 대본 등록 엔드포인트 연동 후 project_id 반환
     */
    private Long generateAiProjectIdForCustom(String scriptText, MultipartFile scriptFile) {
        RestTemplate restTemplate = new RestTemplate();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10_000);
        factory.setReadTimeout(30_000);
        restTemplate.setRequestFactory(factory);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);
        addApiKeyHeader(headers);

        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();

        if (scriptFile != null && !scriptFile.isEmpty()) {
            try {
                byte[] fileBytes = scriptFile.getBytes();
                String filename = scriptFile.getOriginalFilename() != null ? scriptFile.getOriginalFilename() : "script.txt";

                body.add("file", new ByteArrayResource(fileBytes) {
                    @Override
                    public String getFilename() {
                        return filename;
                    }
                });
            } catch (IOException e) {
                throw new RuntimeException("대본 파일 읽기 실패", e);
            }
        }

        if (scriptText != null && !scriptText.isBlank()) {
            body.add("text", scriptText);
        }

        try {
            ResponseEntity<Map> response = restTemplate.postForEntity(
                    AI_BASE_URL + "/api/project/create-custom",
                    new HttpEntity<>(body, headers),
                    Map.class
            );

            Map responseBody = response.getBody();
            if (responseBody == null || !responseBody.containsKey("project_id")) {
                throw new IllegalStateException("AI 서버로부터 프로젝트 ID를 받아오지 못했습니다: " + responseBody);
            }

            return ((Number) responseBody.get("project_id")).longValue();

        } catch (HttpStatusCodeException e) {
            throw new ResponseStatusException(e.getStatusCode(), "AI 서버 커스텀 프로젝트 생성 실패: " + extractDetail(e.getResponseBodyAsString()));
        }
    }
}